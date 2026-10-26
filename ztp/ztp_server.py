#!/usr/bin/env python3
"""vamd ZTP server. Serves the POAP script, serves each device's startup-config LIVE from Infrahub looked up by serial
number, and keeps DcimDevice.ztp_state current on main through the ztp-callback account.

  ztp_server.py <listen ip> <port>

States: pending is set by a person / PC before racking. This server writes
  served    config sent to that serial                                        (GET /config/<serial>)
  complete  after the POAP reload: SSH to the device's management interface (FabricDesign <role>_mgmt_interface) address from
            its config answers with hostname = device name, and 'show version' equals its DcimPlatform.os_version
  failed    the POAP script reported an error (GET /callback/<serial>/failed/<reason>), no verified login within
            FabricDesign.ztp_verify_window_s of the callback, or the running release differs from DcimPlatform.os_version
            (ZTP does not install images; the mismatch is logged as event version-mismatch and in the state reason)
The callback itself (GET /callback/<serial>) only starts the verification; the state stays served until it ends.
Design values are read from Infrahub through ztp_design.py; a missing one ends the device failed with the field named.
Every write also sets ztp_updated (UTC). Writes go directly to main (decision 8 Oct).

Secrets (~/.vamd/secrets.env, ztp/make-secret.sh, ztp/setup_account.py): ZTP_CALLBACK_TOKEN (Infrahub API token of ztp-callback),
CISCOADMIN_HASH (filled into the config, lab/vamd_secrets.py), CISCOADMIN_PASSWORD (the verification login). Without CISCOADMIN_HASH
the username line is dropped (warning event) and the verification logs in with the lab login (as labctl).
env: INFRAHUB_ADDRESS (labctl's default when unset), ZTP_SERIAL_ATTR (serial_number)
"""
import datetime
import hashlib
import http.server
import json
import os
import sys
import threading
import time

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lab"))
import labctl          # noqa: E402  Infrahub client (login + artifact read), the same code the lab uses
import vamd_secrets    # noqa: E402
import ztp_design      # noqa: E402  FabricDesign / DcimPlatform values (fails loudly on a missing value)

LOG = os.path.join(HERE, "ztp-events.jsonl")
ATTR = os.environ.get("ZTP_SERIAL_ATTR", "serial_number")
LISTEN, PORT = sys.argv[1], int(sys.argv[2])


def event(kind, **kw):
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": kind, **kw}
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)


def poap_script():
    body = open(os.path.join(HERE, "poap.py")).read().replace("__SRV__", f"{LISTEN}:{PORT}")
    lines = [l for l in body.splitlines(keepends=True) if not l.startswith("#md5sum")]
    md5 = hashlib.md5("".join(lines).encode()).hexdigest()
    return body.replace("__MD5__", md5).encode()


def device_by_serial(ih, sn):
    q = '{ DcimDevice(%s__value: "%s") { edges { node { id name { value } } } } }' % (ATTR, sn)
    return [(e["node"]["id"], e["node"]["name"]["value"]) for e in ih.gql(q)["DcimDevice"]["edges"]]


def set_state(dev_id, name, state, **kw):
    """Write ztp_state + ztp_updated on main as ztp-callback (API token)."""
    tok = vamd_secrets.load().get("ZTP_CALLBACK_TOKEN")
    if not tok:
        event("state-not-written", device=name, state=state, error="ZTP_CALLBACK_TOKEN not set"); return
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    m = ('mutation { DcimDeviceUpdate(data: {id: "%s", ztp_state: {value: "%s"}, ztp_updated: {value: "%s"}}) { ok } }'
         % (dev_id, state, now))
    try:
        r = httpx.post(f"{labctl.INFRAHUB}/graphql/main", json={"query": m}, headers={"X-INFRAHUB-KEY": tok}, timeout=60).json()
        ok = bool(r.get("data", {}) and r["data"].get("DcimDeviceUpdate", {}).get("ok"))
        event("state", device=name, state=state, written=ok, **kw, **({} if ok else {"error": str(r.get("errors"))[:300]}))
    except Exception as e:
        event("state", device=name, state=state, written=False, error=repr(e)[:300], **kw)


def mgmt_target(ih, dev_id, cfg):
    """(address of the device's management interface in its config, why not) - the interface is FabricDesign.<role>_mgmt_interface."""
    try:
        ifname = ztp_design.mgmt_interface(ih, ztp_design.device(ih, dev_id=dev_id))
    except (Exception, SystemExit) as e:
        return None, f"design value not readable: {e}"[:200]
    ip = ztp_design.mgmt_address(cfg, ifname)
    return ip, (None if ip else f"no {ifname} address in the config")


def verify(dev_id, name, sn, ip):
    """After the callback: SSH every ztp_verify_poll_s until the device answers with its own hostname, then compare its running
    release ('show version') with DcimPlatform.os_version: equal -> complete, different -> failed. No verified login
    within ztp_verify_window_s -> failed."""
    from netmiko import ConnectHandler
    vals = vamd_secrets.load()
    login = vals.get("CISCOADMIN_PASSWORD") and vals.get("CISCOADMIN_HASH")
    try:
        ih = labctl.Infrahub("main")
        d = ztp_design.design_values(ih, "ztp_verify_window_s", "ztp_verify_poll_s", *(["local_admin_username"] if login else []))
        want = ztp_design.os_version(ztp_design.device(ih, dev_id=dev_id))
    except (Exception, SystemExit) as e:
        return set_state(dev_id, name, "failed", serial=sn, reason=f"design value not readable: {e}"[:200])
    max_s, every_s = d["ztp_verify_window_s"], d["ztp_verify_poll_s"]
    if login:
        user, pw = d["local_admin_username"], vals["CISCOADMIN_PASSWORD"]
    else:
        user, pw = labctl.NXOS_USER, labctl.NXOS_PASS
    event("verify-start", serial=sn, device=name, ip=ip, user=user, max_s=max_s, os_version=want)
    start, last = time.time(), ""
    while time.time() - start < max_s:
        try:
            with ConnectHandler(device_type="cisco_nxos", host=ip, username=user, password=pw, conn_timeout=20, fast_cli=False) as c:
                out = c.send_command("show hostname").strip()
                ver = c.send_command("show version") if out.split(".")[0] == name else ""
            if out.split(".")[0] == name:
                running = ztp_design.running_version(ver)
                if running != want:
                    line = next((l.strip() for l in ver.splitlines() if "NXOS: version" in l), "no 'NXOS: version' line")
                    event("version-mismatch", serial=sn, device=name, running=running, os_version=want, show_version=line[:120])
                    return set_state(dev_id, name, "failed", serial=sn,
                                     reason=f"release mismatch: running {running}, DcimPlatform.os_version {want}")
                return set_state(dev_id, name, "complete", serial=sn, after_s=round(time.time() - start), os_version=running)
            last = f"hostname {out!r}"
        except Exception as e:
            last = type(e).__name__
        time.sleep(every_s)
    set_state(dev_id, name, "failed", serial=sn, reason=f"not verified in {max_s} s ({last})")


SERVED = {}    # serial -> (device id, name, mgmt ip, why no ip) of the last config served


class H(http.server.BaseHTTPRequestHandler):
    def send(self, code, body, ctype="text/plain"):
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        parts = self.path.strip("/").split("/")
        try:
            if parts == ["poap.py"]:
                event("script", client=self.client_address[0]); return self.send(200, poap_script())
            if len(parts) >= 2 and parts[0] in ("config", "callback"):
                sn = parts[1]
                ih = labctl.Infrahub("main")
                devs = device_by_serial(ih, sn)
                if len(devs) != 1:
                    event(parts[0] + "-refused", serial=sn, matches=[d[1] for d in devs]); return self.send(404, f"serial {sn}: {len(devs)} devices\n".encode())
                dev_id, name = devs[0]
                if parts[0] == "config":
                    body, meta = ih.artifact("startup-config", name)
                    body, warns = vamd_secrets.fill(body)
                    for w in warns:
                        event("secret-warning", serial=sn, device=name, warning=w)
                    SERVED[sn] = (dev_id, name, *mgmt_target(ih, dev_id, body))
                    event("config", serial=sn, device=name, artifact_id=meta["id"], checksum=meta["checksum"], bytes=len(body))
                    set_state(dev_id, name, "served", serial=sn)
                    return self.send(200, body.encode())
                if len(parts) >= 3 and parts[2] == "failed":
                    reason = "/".join(parts[3:])[:200]
                    event("callback-failed", serial=sn, device=name, reason=reason)
                    set_state(dev_id, name, "failed", serial=sn, reason=reason)
                    return self.send(200, b"ok\n")
                _, _, ip, why = SERVED.get(sn, (None, None, None, "callback without a served config"))
                event("callback", serial=sn, device=name, verify_ip=ip)
                if not ip:
                    set_state(dev_id, name, "failed", serial=sn, reason=why)
                else:
                    threading.Thread(target=verify, args=(dev_id, name, sn, ip), daemon=True).start()
                return self.send(200, b"ok\n")
        except SystemExit as e:
            event("error", path=self.path, error=str(e)); return self.send(502, b"infrahub error\n")
        except Exception as e:
            event("error", path=self.path, error=repr(e)); return self.send(500, b"error\n")
        event("unknown", path=self.path); self.send(404, b"not found\n")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    event("start", listen=LISTEN, port=PORT, infrahub=labctl.INFRAHUB, serial_attr=ATTR)
    http.server.ThreadingHTTPServer((LISTEN, PORT), H).serve_forever()
