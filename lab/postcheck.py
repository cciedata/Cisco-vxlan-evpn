#!/usr/bin/env python3
"""Post-checks (Nornir + netmiko): compare the running lab with the lab-expected artifact.

  postcheck.py <site>          run every check, write lab/build/<site>/reports/postcheck-<time>.{txt,json}, exit 0 only if all pass
  postcheck.py <site> --plan   list every check and the command that will answer it; touches no device

Checks (all from expected.json, nothing typed by hand):
  bgp    every session: node, vrf, peer, remote AS, state Established
  bfd    every session marked bfd: a BFD session to the peer is Up (NX-OS, FRR; IOS XR read from 'show bfd session')
  route  every route: present in the node's VRF table
  ping   every ping from a test host: at least PING_MIN_OK of PING_COUNT replies
Runner: Nornir runs one task per node in parallel; the inventory is the
lab-topology artifact (lab/build/<site>/topology.yml: node, kind, mgmt-ipv4). Drivers: NX-OS over SSH with netmiko
('show ... | json'), FRR via 'docker exec vtysh ... json', IOS XR via 'docker exec xr_cli' text (no SSH in the stand-ins).
NX-API reads are replaced by netmiko; failtests.py keeps NX-API only for its break/restore actions.
"""
import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
import warnings

import httpx  # noqa: F401  (failtests.py uses postcheck.httpx for its NX-API break actions)
import yaml
from netmiko import ConnectHandler
from nornir.core import Nornir
from nornir.core.inventory import ConnectionOptions, Defaults, Groups, Host, Hosts, Inventory
from nornir.core.plugins.connections import ConnectionPluginRegister
from nornir.plugins.runners import ThreadedRunner

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
NXOS_USER, NXOS_PASS = "admin", "admin"         # vrnetlab N9Kv default login
PING_COUNT, PING_MIN_OK, PING_WAIT_S = 3, 2, 2  # 2 of 3 (the first may be lost to ARP)
XR_CLI = "/pkg/bin/xr_cli.sh"                   # ❓ path inside XRd, confirm on first run
NORNIR_WORKERS = 10                             # FU-11: parallel node tasks (laptop: 1-2 nodes; AWS lab: 18)
SSH_READ_TIMEOUT = 120                          # FU-11: seconds per NX-OS show over SSH
NETMIKO_PLATFORM = {"cisco_n9kv": "cisco_nxos"}  # FU-11: netmiko device_type per containerlab kind


def walk(obj, ctx=None):
    """Yield (dict, context) for every dict in an NX-OS JSON tree; context carries the nearest vrf name."""
    ctx = dict(ctx or {})
    if isinstance(obj, dict):
        for k in ("vrf-name-out", "vrf_name", "vrf-name", "vrfname"):
            if k in obj and isinstance(obj[k], str):
                ctx["vrf"] = obj[k]
        yield obj, ctx
        for v in obj.values():
            yield from walk(v, ctx)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v, ctx)


class Node:
    def __init__(self, name, spec, lab):
        self.name, self.kind, self.ip = name, spec["kind"], spec.get("mgmt-ipv4")
        self.container = f"clab-{lab}-{name}"
        self._cache = {}

    # ---- transport
    def ssh(self):
        """netmiko session to the node's mgmt address: the Nornir connection when run from run_nornir(), else its own."""
        if getattr(self, "_conn", None) is None:
            self._conn = ConnectHandler(device_type=NETMIKO_PLATFORM[self.kind], host=self.ip, username=NXOS_USER,
                                        password=NXOS_PASS, fast_cli=False, conn_timeout=30)
        return self._conn

    def nx(self, cmd):
        if cmd not in self._cache:
            try:
                out = self.ssh().send_command(f"{cmd} | json", read_timeout=SSH_READ_TIMEOUT)
                self._cache[cmd] = json.loads(out) if out.strip() else {}
            except Exception as e:
                self._cache[cmd] = {"_error": f"{type(e).__name__}: {str(e)[:200]}"}
        return self._cache[cmd]

    def sh(self, *argv):
        key = " ".join(argv)
        if key not in self._cache:
            p = subprocess.run(["docker", "exec", self.container, *argv], capture_output=True, text=True, timeout=120)
            self._cache[key] = p.stdout if p.returncode == 0 else f"_error rc={p.returncode} {p.stderr[:300]}"
        return self._cache[key]

    def vtysh(self, cmd):
        out = self.sh("vtysh", "-c", cmd)
        try:
            return json.loads(out)
        except Exception:
            return {"_error": out[:300]}

    def xr(self, cmd):
        return self.sh(XR_CLI, cmd)

    # ---- what each check runs (for --plan and for the report)
    def how(self, check, vrf=None, prefix=None, peer=None):
        if self.kind == "cisco_n9kv":
            return {"bgp": "show bgp sessions vrf all", "bfd": "show bfd neighbors vrf all",
                    "route": f"show ip route {prefix} vrf {vrf}"}[check] + " | json (SSH, netmiko)"
        if self.kind == "linux":
            v = "" if vrf in (None, "default") else f" vrf {vrf}"
            return {"bgp": "vtysh: show bgp vrf all summary json", "bfd": "vtysh: show bfd peers json",
                    "route": f"vtysh: show ip route{v} {prefix} json"}[check]
        if self.kind == "cisco_xrd":
            return {"bgp": "xr: show bgp summary", "bfd": "xr: show bfd session",
                    "route": f"xr: show route {prefix}"}[check]
        return "?"

    # ---- checks
    def bgp(self, vrf, peer):
        if self.kind == "cisco_n9kv":
            for d, ctx in walk(self.nx("show bgp sessions vrf all")):
                nid = d.get("neighbor-id") or d.get("neighborid")
                if nid == peer and ctx.get("vrf", "default") == vrf:
                    return d.get("state") == "Established", d.get("state")
            return False, "session not found"
        if self.kind == "linux":
            data = self.vtysh("show bgp vrf all summary json")
            for vname, vd in data.items():
                if vname != vrf and not (vrf == "default" and vname == "default"):
                    continue
                for afi in vd.values() if isinstance(vd, dict) else []:
                    if isinstance(afi, dict) and peer in afi.get("peers", {}):
                        st = afi["peers"][peer].get("state")
                        return st == "Established", st
            return False, data.get("_error", "session not found")
        if self.kind == "cisco_xrd":
            out = self.xr("show bgp summary")
            for line in out.splitlines():
                f = line.split()
                if f and f[0] == peer:
                    return f[-1].isdigit(), f[-1]
            return False, out[:120] if out.startswith("_error") else "session not found"
        return False, "no driver"

    def bfd(self, vrf, peer):
        if self.kind == "cisco_n9kv":
            for d, ctx in walk(self.nx("show bfd neighbors vrf all")):
                if peer in (d.get("dest_ip_addr"), d.get("neigh"), d.get("dst_ip_addr")):
                    st = d.get("state") or d.get("local_state") or d.get("remote_state")
                    return str(st).lower() == "up", st
            return False, "no BFD session"
        if self.kind == "linux":
            data = self.vtysh("show bfd peers json")
            for p in data if isinstance(data, list) else []:
                if p.get("peer") == peer:
                    return p.get("status") == "up", p.get("status")
            return False, "no BFD session"
        if self.kind == "cisco_xrd":
            out = self.xr("show bfd session")
            for line in out.splitlines():
                f = line.split()
                if peer in f:
                    return "UP" in f, " ".join(f[-3:])
            return False, "no BFD session"
        return False, "no driver"

    def route(self, vrf, prefix):
        if self.kind == "cisco_n9kv":
            body = self.nx(f"show ip route {prefix} vrf {vrf}")
            found = any(d.get("ipprefix") == prefix for d, _ in walk(body))
            return found, "present" if found else "absent"
        if self.kind == "linux":
            v = "" if vrf == "default" else f" vrf {vrf}"
            data = self.vtysh(f"show ip route{v} {prefix} json")
            found = prefix in data
            return found, "present" if found else data.get("_error", "absent")
        if self.kind == "cisco_xrd":
            out = self.xr(f"show route {prefix}")
            found = f"Routing entry for {prefix}" in out
            return found, "present" if found else "absent"
        return False, "no driver"


def ping(lab, src, dst):
    p = subprocess.run(["docker", "exec", f"clab-{lab}-{src}", "ping", "-c", str(PING_COUNT), "-W", str(PING_WAIT_S), dst],
                       capture_output=True, text=True, timeout=60)
    m = re.search(r"(\d+) packets received", p.stdout)
    got = int(m.group(1)) if m else 0
    return got >= PING_MIN_OK, f"{got}/{PING_COUNT} replies"


def one(lab, nodes, check):
    kind, node, vrf, what, why = check
    if what is None:
        return {"check": kind, "node": node, "vrf": vrf, "target": None, "why": why, "pass": False,
                "got": "expectation has no target (lab-expected artifact)"}
    try:
        ok, got = ping(lab, node, what) if kind == "ping" else getattr(nodes[node], kind)(vrf, what)
    except Exception as e:
        ok, got = False, f"error {type(e).__name__}: {str(e)[:160]}"
    return {"check": kind, "node": node, "vrf": vrf, "target": what, "why": why, "pass": ok, "got": got}


def inventory(nodes):
    """Nornir inventory from the lab topology: one host per node that has checks; platform = netmiko device_type."""
    hosts = Hosts()
    for name, n in nodes.items():
        hosts[name] = Host(name=name, hostname=n.ip, username=NXOS_USER, password=NXOS_PASS,
                           platform=NETMIKO_PLATFORM.get(n.kind), data={"kind": n.kind},
                           connection_options={"netmiko": ConnectionOptions(extras={"fast_cli": False, "conn_timeout": 30})})
    return Inventory(hosts=hosts, groups=Groups(), defaults=Defaults())


def run_nornir(lab, nodes, checks):
    """One Nornir task per node runs that node's checks in order; nodes run in parallel. Results keep the plan order."""
    by_node = {}
    for i, c in enumerate(checks):
        by_node.setdefault(c[1], []).append((i, c))
    ConnectionPluginRegister.auto_register()       # nornir_netmiko's "netmiko" connection plugin (entry point)
    nr = Nornir(inventory=inventory({n: nodes[n] for n in by_node}), runner=ThreadedRunner(num_workers=NORNIR_WORKERS))

    def task(task):
        node = nodes[task.host.name]
        if node.kind in NETMIKO_PLATFORM:
            node._conn = task.host.get_connection("netmiko", task.nornir.config)
        return [(i, one(lab, nodes, c)) for i, c in by_node[task.host.name]]

    agg = nr.run(task=task, name="postcheck")
    out = {}
    for host, multi in agg.items():
        if multi.failed:
            exc = multi[0].exception
            for i, c in by_node[host]:
                out[i] = {"check": c[0], "node": c[1], "vrf": c[2], "target": c[3], "why": c[4], "pass": False,
                          "got": f"error {type(exc).__name__}: {str(exc)[:160]}"}
        else:
            out.update(dict(multi[0].result))
    nr.close_connections()
    return [out[i] for i in range(len(checks))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("site")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--nodes", help="comma-separated subset (a partial lab): only checks run on these nodes")
    a = ap.parse_args()
    base = os.path.join(BUILD, a.site)
    topo = yaml.safe_load(open(os.path.join(base, "topology.yml")))
    exp = json.load(open(os.path.join(base, "expected.json")))
    lab = topo["name"]
    nodes = {n: Node(n, s, lab) for n, s in topo["topology"]["nodes"].items()}
    checks = []
    for s in exp["bgp_sessions"]:
        checks.append(("bgp", s["node"], s["vrf"], s["peer"], f"AS {s['remote_as']} {s['description']} ({s['afi']})"))
        if s.get("bfd"):
            checks.append(("bfd", s["node"], s["vrf"], s["peer"], s["description"]))
    for r in exp["routes"]:
        checks.append(("route", r["node"], r["vrf"], r["prefix"], r["why"]))
    for pg in exp["pings"]:
        checks.append(("ping", pg["from"], None, pg["to"], pg["why"]))
    skipped = 0
    if a.nodes:
        only = set(a.nodes.split(","))
        kept = [c for c in checks if c[1] in only]
        skipped = len(checks) - len(kept)
        checks = kept
        print(f"node subset {sorted(only)}: {len(checks)} checks kept, {skipped} skipped")
    missing = sorted({c[1] for c in checks if c[1] not in nodes})
    if missing:
        print(f"STOP: expected.json names nodes not in the topology: {missing}")
        sys.exit(1)

    if a.plan:
        counts = {}
        for kind, node, vrf, what, why in checks:
            counts[kind] = counts.get(kind, 0) + 1
            how = f"ping -c {PING_COUNT} {what}" if kind == "ping" else nodes[node].how(kind, vrf=vrf, prefix=what, peer=what)
            if what is None:
                how = "NONE: expectation has no target (lab-expected artifact)"
            print(f"{kind:<5} {node:<15} {str(vrf or ''):<10} {str(what):<18} via {how}")
        print("plan: " + ", ".join(f"{k} {v}" for k, v in counts.items()) + f", total {len(checks)}")
        return

    t0 = time.time()
    results = run_nornir(lab, nodes, checks)
    fails = [r for r in results if not r["pass"]]
    os.makedirs(os.path.join(base, "reports"), exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    lines = [f"{a.site} post-check {stamp}: {len(results) - len(fails)} of {len(results)} pass ({time.time() - t0:.0f}s)"
             + (f"; partial lab {a.nodes}, {skipped} checks skipped" if a.nodes else "")]
    for kind in ("bgp", "bfd", "route", "ping"):
        sub = [r for r in results if r["check"] == kind]
        lines.append(f"  {kind:<5} {sum(r['pass'] for r in sub)} of {len(sub)}")
    for r in fails:
        lines.append(f"FAIL {r['check']:<5} {r['node']:<15} {str(r['vrf'] or ''):<10} {str(r['target']):<18} got: {r['got']}  ({r['why']})")
    txt = "\n".join(lines) + "\n"
    open(os.path.join(base, "reports", f"postcheck-{stamp}.txt"), "w").write(txt)
    json.dump(results, open(os.path.join(base, "reports", f"postcheck-{stamp}.json"), "w"), indent=2)
    print(txt, end="")
    sys.exit(0 if not fails else 6)


if __name__ == "__main__":
    main()
