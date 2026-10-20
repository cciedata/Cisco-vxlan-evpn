#!/usr/bin/env python3
"""vamd ZTP server (Phase 3b). Serves the POAP script, serves each device's startup-config LIVE from Infrahub looked up by serial
number (Z-04, Z-05), and keeps DcimDevice.ztp_state current on main through the ztp-callback account (Z-06, Z-08).

  ztp_server.py <listen ip> <port>

States (decided 8 Oct): pending is set by a person / PC before racking. This server writes
  served    config sent to that serial                                        (GET /config/<serial>)
  complete  after the POAP reload: SSH to the device's mgmt0 address from its config answers with hostname = device name
  failed    the POAP script reported an error (GET /callback/<serial>/failed/<reason>), or no verified login within 30 min
The callback itself (GET /callback/<serial>) only starts the verification; the state stays served until it ends.
Every write also sets ztp_updated (UTC). Writes go directly to main (decision 8 Oct).

Secrets (~/.vamd/secrets.env, ztp/make-secret.sh, ztp/setup_account.py): ZTP_CALLBACK_TOKEN (Infrahub API token of ztp-callback),
VAMDADMIN_HASH (filled into the config, lab/vamd_secrets.py), VAMDADMIN_PASSWORD (the verification login). Without VAMDADMIN_HASH
the username line is dropped (warning event) and the verification logs in with the lab login (labctl P3D-07).
env: INFRAHUB_ADDRESS (labctl's default when unset), ZTP_SERIAL_ATTR (serial_number)
"""
import datetime
import hashlib
import http.server
import json
import os
import re
import sys
import threading
import time

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lab"))
import labctl          # noqa: E402  Infrahub client (login + artifact read), the same code the lab uses
import vamd_secrets    # noqa: E402

LOG = os.path.join(HERE, "ztp-events.jsonl")
ATTR = os.environ.get("ZTP_SERIAL_ATTR", "serial_number")
VERIFY_MAX_S = 1800        # decided 8 Oct: 30 min after the callback, then failed
VERIFY_EVERY_S = 30
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


def mgmt_target(cfg):
    m = re.search(r"^interface mgmt0\n(?:  .*\n)*?\s+ip address (\d+\.\d+\.\d+\.\d+)/\d+", cfg, re.M)
    return m.group(1) if m else None


def verify(dev_id, name, sn, ip):
    """After the callback: SSH every 30 s until the device answers with its own hostname (complete) or 30 min pass (failed)."""
    from netmiko import ConnectHandler
    vals = vamd_secrets.load()
    user, pw = ("vamdadmin", vals["VAMDADMIN_PASSWORD"]) if vals.get("VAMDADMIN_PASSWORD") and vals.get("VAMDADMIN_HASH") \
        else (labctl.NXOS_USER, labctl.NXOS_PASS)
    event("verify-start", serial=sn, device=name, ip=ip, user=user, max_s=VERIFY_MAX_S)
    start, last = time.time(), ""
    while time.time() - start < VERIFY_MAX_S:
        try:
            with ConnectHandler(device_type="cisco_nxos", host=ip, username=user, password=pw, conn_timeout=20, fast_cli=False) as c:
                out = c.send_command("show hostname").strip()
            if out.split(".")[0] == name:
                return set_state(dev_id, name, "complete", serial=sn, after_s=round(time.time() - start))
            last = f"hostname {out!r}"
        except Exception as e:
            last = type(e).__name__
        time.sleep(VERIFY_EVERY_S)
    set_state(dev_id, name, "failed", serial=sn, reason=f"not verified in {VERIFY_MAX_S} s ({last})")


SERVED = {}    # serial -> (device id, name, mgmt ip) of the last config served


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
                    SERVED[sn] = (dev_id, name, mgmt_target(body))
                    event("config", serial=sn, device=name, artifact_id=meta["id"], checksum=meta["checksum"], bytes=len(body))
                    set_state(dev_id, name, "served", serial=sn)
                    return self.send(200, body.encode())
                if len(parts) >= 3 and parts[2] == "failed":
                    reason = "/".join(parts[3:])[:200]
                    event("callback-failed", serial=sn, device=name, reason=reason)
                    set_state(dev_id, name, "failed", serial=sn, reason=reason)
                    return self.send(200, b"ok\n")
                ip = SERVED.get(sn, (None, None, None))[2]
                event("callback", serial=sn, device=name, verify_ip=ip)
                if not ip:
                    set_state(dev_id, name, "failed", serial=sn, reason="callback without a served config / no mgmt0 address")
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
