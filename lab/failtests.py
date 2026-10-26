#!/usr/bin/env python3
"""Failure / behavior tests (tests 1-15; test 4 removed) on a deployed site lab.

  failtests.py <site> --list            the catalog: what each test breaks, what it expects, how it restores
  failtests.py <site> --run 1,3,5       run some tests (default: all, in order)

Per test: baseline post-check must pass -> start a continuous ping from the PROD host to an internet host -> break ->
wait -> check the expectations -> restore -> wait -> full post-check must pass again. Outage (8 Oct): from the moment the
break is applied to the first ping reply received after it; no reply before the checks end = open-ended (fails any outage limit).
The old metric (longest run of lost replies x interval) is kept as longest_gap_s for reference.
Session checks (8 Oct): a "sessions up" check compares against the sessions that were up just before the break, so a session
already down before the test cannot fail it (the baseline post-check still has to pass first).
Results: lab/build/<site>/reports/failtests-<time>.{txt,json}.

How things break: link down inside the node (NX-API 'shutdown', 'ip link set down' in FRR, XR CLI when ISP_LAB_STANDIN = xrd);
node death = 'docker pause' (QEMU/FRR freeze, so neighbors detect it by BFD/hold timers, like a power loss);
silent failure = netem 100% loss on a stand-in port (link stays up).
Expectations reuse lab-expected entries (bgp / route / ping) plus a few explicit 'down' checks; nothing is typed per device
beyond the node and port each test touches, which come from the topology and the configs.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import labctl  # noqa: E402  (Infrahub client: login + GraphQL, the same code the lab uses)
import postcheck  # noqa: E402  (Node drivers, ping, walk)
import yaml  # noqa: E402

PING_INTERVAL = 0.2           # continuous ping every 200 ms
SETTLE_S = 45                 # wait after a break before checking (BFD detection = bfd_interval_ms x bfd_multiplier, BGP, ECMP)
RESTORE_S = 120               # wait after restore before the full post-check
# Design values: read from Infrahub (FabricDesign, FabricVRF) at start-up, see load_design(); a missing value stops the run
DESIGN_FIELDS = ("edge_vrrp_preempt_delay_s",    # test 14: preempt delay in the ISP router config
                 "vpc_peer_link_port_channel",   # test 6: peer-link port-channel number in the rendered config
                 "vpc_keepalive_vrf",            # test 7: keepalive VRF in the rendered config
                 "transit_vrf")                  # tests 11, 12: TRANSIT VRF name (and its FortiOS VRF id = the FRR stand-in's vrf<id>)
FRR_BGP_RESTART = ("kill -9 $(cat /var/run/frr/bgpd.pid); sleep 3; /usr/lib/frr/bgpd -d -A 127.0.0.1 -K; "  # I-23: test 13 on an FRR
                   "sleep 2; vtysh -f /etc/frr/frr.conf")   # stand-in: kill bgpd (pid file), start it with -K (graceful restart), re-apply


def load_design(branch):
    """FabricDesign fields this script uses, plus the TRANSIT VRF's FortiOS VRF id, over GraphQL (labctl's Infrahub client)."""
    ih = labctl.Infrahub(branch)
    fd = ih.gql("{ FabricDesign { count edges { node { %s } } } }" % " ".join(f"{f} {{ value }}" for f in DESIGN_FIELDS))["FabricDesign"]
    if fd["count"] != 1:
        labctl.die(f"FabricDesign: expected exactly 1 object, found {fd['count']}; fix it in Infrahub (branch {branch})")
    design = {}
    for f in DESIGN_FIELDS:
        value = (fd["edges"][0]["node"].get(f) or {}).get("value")
        if value is None:
            labctl.die(f"FabricDesign.{f} is empty; fix it in Infrahub (branch {branch})")
        design[f] = value
    vrfs = ih.gql('{ FabricVRF(name__value: "%s") { edges { node { fortios_vrf_id { value } } } } }' % design["transit_vrf"])["FabricVRF"]["edges"]
    if len(vrfs) != 1 or vrfs[0]["node"]["fortios_vrf_id"]["value"] is None:
        labctl.die(f"FabricVRF {design['transit_vrf']}: fortios_vrf_id is empty or the VRF is missing; fix it in Infrahub (branch {branch})")
    design["transit_fw_vrf"] = f"vrf{vrfs[0]['node']['fortios_vrf_id']['value']}"
    return design


def test_host_vrf(lab, branch):
    """Tests 3, 10: VRF of the PROD test host's access segment - FabricSegment.vrf of the access port(s) on the node the
    host plugs into (from the topology), over GraphQL. Not exactly one VRF stops the run."""
    far = sorted({fn for _, fn, _ in lab.links(lab.host)})
    if len(far) != 1:
        labctl.die(f"{lab.host}: expected one link in the topology, found nodes {far}")
    q = ('{ InterfacePhysical(device__name__value: "%s", role__value: "access") { edges { node { name { value } '
         'access_segment { node { name { value } vrf { node { name { value } } } } } } } } }' % far[0])
    edges = labctl.Infrahub(branch).gql(q)["InterfacePhysical"]["edges"]
    def vrf_of(port):
        seg = (port.get("access_segment") or {}).get("node") or {}
        return (((seg.get("vrf") or {}).get("node") or {}).get("name") or {}).get("value")
    vrfs = sorted({vrf_of(e["node"]) for e in edges} - {None})
    if len(vrfs) != 1:
        labctl.die(f"{far[0]}: access ports with a segment VRF give {vrfs or 'none'}, expected exactly one; fix it in Infrahub (branch {branch})")
    return vrfs[0]


def own_asn(lab, node):
    """The node's own BGP AS (DcimDevice.asn), from lab-expected node_asn (transforms/lab.py)."""
    asn = lab.exp.get("node_asn", {}).get(node)
    if asn is None:
        labctl.die(f"{node}: no own ASN in lab-expected node_asn; re-run 'labctl.py fetch {lab.site}'")
    return asn


def sh(*argv, timeout=120):
    p = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


class Lab:
    def __init__(self, site):
        base = os.path.join(HERE, "build", site)
        self.site, self.base = site, base
        self.topo = yaml.safe_load(open(os.path.join(base, "topology.yml")))
        self.exp = json.load(open(os.path.join(base, "expected.json")))
        self.name = self.topo["name"]
        self.nodes = {n: postcheck.Node(n, s, self.name) for n, s in self.topo["topology"]["nodes"].items()}
        self.host = f"{site}-HOST-PROD"
        self.inet = [p["to"] for p in self.exp["pings"] if p["to"].startswith("198.18.")]
        self.design = {}             # load_design(), filled in main()

    def ctr(self, node):
        return f"clab-{self.name}-{node}"

    def links(self, node):
        """[(my port, far node, far port)] from the topology (containerlab names)."""
        out = []
        for l in self.topo["topology"]["links"]:
            a, b = l["endpoints"]
            for mine, far in ((a, b), (b, a)):
                n, p = mine.split(":")
                if n == node:
                    fn, fp = far.split(":")
                    out.append((p, fn, fp))
        return out

    def cfg_ports(self, node, pattern):
        """containerlab ports of the Ethernet interfaces whose block in the node's rendered config matches pattern."""
        spec = self.topo["topology"]["nodes"][node]
        text = open(os.path.join(self.base, spec["startup-config"])).read()
        out = []
        for blk in re.split(r"\n(?=\S)", text):
            m = re.match(r"interface Ethernet1/(\d+)\n", blk + "\n")
            if m and re.search(pattern, blk):
                out.append(f"eth{m.group(1)}")
        return out

    def by_role(self, prefix):
        return sorted(n for n in self.nodes if n.startswith(f"{self.site}-{prefix}"))

    # ---- breaking things
    def nxos_if(self, node, clab_port, state):
        m = re.fullmatch(r"eth(\d+)", clab_port)
        ifname = f"Ethernet1/{m.group(1)}"
        api = postcheck.httpx.post(f"https://{self.nodes[node].ip}/ins", auth=(postcheck.NXOS_USER, postcheck.NXOS_PASS), verify=False,
                                   timeout=60, headers={"content-type": "application/json"},
                                   content=json.dumps({"ins_api": {"version": "1.0", "type": "cli_conf", "chunk": "0", "sid": "1",
                                                                   "input": f"interface {ifname} ; {'shutdown' if state == 'down' else 'no shutdown'}",
                                                                   "output_format": "json"}}))
        return f"{node} {ifname} {state} (NX-API {api.status_code})"

    def linux_if(self, node, port, state):
        rc, out = sh("docker", "exec", self.ctr(node), "ip", "link", "set", port, state)
        return f"{node} {port} {state} (rc {rc})"

    def xr_if(self, node, port, state):
        ifname = "GigabitEthernet" + port[2:].replace("-", "/")
        cmd = f"configure\ninterface {ifname}\n{'shutdown' if state == 'down' else 'no shutdown'}\ncommit\nend\n"
        rc, out = sh("docker", "exec", self.ctr(node), postcheck.XR_CLI, cmd)
        return f"{node} {ifname} {state} (rc {rc})"

    def port(self, node, port, state):
        kind = self.nodes[node].kind
        return {"cisco_n9kv": self.nxos_if, "linux": self.linux_if, "cisco_xrd": self.xr_if}[kind](node, port, state)

    def pause(self, node, on=True):
        rc, out = sh("docker", "pause" if on else "unpause", self.ctr(node))
        return f"{node} {'paused' if on else 'unpaused'} (rc {rc})"

    def netem(self, node, port, on=True):
        argv = ["tc", "qdisc", "add" if on else "del", "dev", port, "root"] + (["netem", "loss", "100%"] if on else [])
        rc, out = sh("docker", "exec", self.ctr(node), *argv)
        return f"{node} {port} netem loss 100% {'on' if on else 'off'} (rc {rc})"

    def vtysh(self, node, *lines):
        argv = ["docker", "exec", self.ctr(node), "vtysh"]
        for l in lines:
            argv += ["-c", l]
        rc, out = sh(*argv)
        return f"{node} vtysh {' | '.join(lines)} (rc {rc})"


class Pinger(threading.Thread):
    """Continuous ping from the PROD host; records (time, ok) per probe."""

    def __init__(self, lab, dst):
        super().__init__(daemon=True)
        self.lab, self.dst, self.log, self.stop_ = lab, dst, [], False

    def run(self):
        p = subprocess.Popen(["docker", "exec", self.lab.ctr(self.lab.host), "ping", "-i", str(PING_INTERVAL), "-W", "1", "-O" if False else "-n", self.dst],
                             stdout=subprocess.PIPE, text=True)
        seq_seen, t0 = set(), time.time()
        self.t0 = t0                                        # seq n is sent at about t0 + n x interval
        self.replies = []                                   # receive times of every reply (8 Oct outage method)
        while not self.stop_:
            line = p.stdout.readline()
            if not line:
                break
            m = re.search(r"seq=(\d+)", line)
            if m:
                seq_seen.add(int(m.group(1)))
                self.replies.append((time.time(), int(m.group(1))))
        p.terminate()
        self.seen = seq_seen
        self.sent = int((time.time() - t0) / PING_INTERVAL)

    def outage_since(self, t_break, t_end):
        """8 Oct: seconds from the break to the first reply received after it; (None, open-ended text) when none came.
        Only replies to probes sent after the break count, so a reply already in flight at the break is not taken as recovery."""
        after = [t for t, seq in getattr(self, "replies", []) if t > t_break and self.t0 + seq * PING_INTERVAL >= t_break]
        if not after:
            return None, f"open-ended: no reply in {round(t_end - t_break, 1)} s after the break"
        return round(after[0] - t_break, 1), ""

    def outage(self):
        """Longest run of missing sequence numbers x interval (BusyBox ping prints only replies)."""
        if not getattr(self, "seen", None):
            return None
        top = max(self.seen)
        longest = run = 0
        for s in range(top + 1):
            run = run + 1 if s not in self.seen else 0
            longest = max(longest, run)
        return round(longest * PING_INTERVAL, 1)


def bgp(lab, node, peer, vrf="default"):
    return lab.nodes[node].bgp(vrf, peer)


def sessions(lab, node=None, desc=None, vrf=None):
    return [s for s in lab.exp["bgp_sessions"] if (node is None or s["node"] == node)
            and (desc is None or s["description"].startswith(desc)) and (vrf is None or s["vrf"] == vrf)]


class UpBefore:
    """8 Oct: a 'sessions stay up' check. prime() (just before the break) records which of the expected sessions are up;
    the check then needs every one of those up. Sessions already down before the break are listed, not failed."""

    def __init__(self, lab, ss, what):
        self.lab, self.ss, self.what, self.before = lab, ss, what, None

    def up(self, s):
        return bgp(self.lab, s["node"], s["peer"], s["vrf"])[0]

    def prime(self):
        self.before = [s for s in self.ss if self.up(s)]

    def __call__(self):
        base = self.ss if self.before is None else self.before
        if not base:
            return False, f"0 of {len(self.ss)} {self.what} were up before the break: nothing to compare"
        down = [f"{s['node']}/{s['vrf']}/{s['peer']}" for s in base if not self.up(s)]
        was = [f"{s['node']}/{s['vrf']}/{s['peer']}" for s in self.ss if self.before is not None and s not in self.before]
        return not down, (f"{len(base) - len(down)} of {len(base)} up-before {self.what} up" + (f"; DOWN {down}" if down else "")
                          + (f"; already down before the break (not counted): {was}" if was else ""))


def catalog(lab):
    """Each test: id, title, break steps, restore steps, expectations [(label, fn -> (ok, got))], outage limit (s) or None."""
    S = lab.site
    isp = lab.by_role("ISP")
    isp_routers = [n for n in isp if re.fullmatch(rf"{S}-ISP\d", n)]
    carriers = [n for n in isp if re.fullmatch(rf"{S}-ISP-[A-Z]", n)]
    spines, bls, sls = lab.by_role("S"), lab.by_role("BL"), lab.by_role("SL")
    spines = [n for n in spines if re.fullmatch(rf"{S}-S\d", n)]
    leaf = f"{S}-L11"
    intfw, inetfw = f"{S}-INTFW1", f"{S}-INETFW1"
    a_host, b_host = (lab.inet + [None, None])[:2]
    D = lab.design
    transit, fw_vrf, preempt_s = D["transit_vrf"], D["transit_fw_vrf"], D["edge_vrrp_preempt_delay_s"]
    host_vrf = D["host_vrf"]

    def link_to(node, far_prefix):
        return next(((p, fn, fp) for p, fn, fp in lab.links(node) if fn.startswith(far_prefix)), None)

    def est(node, desc):
        return UpBefore(lab, sessions(lab, node, desc), f"session(s) {desc}")

    def fw_sessions(node):
        return [s for s in sessions(lab, node) if s["vrf"] != "default"]

    def fw_up(node):
        return UpBefore(lab, fw_sessions(node), "FW session(s)")

    def fw_down(node):
        ss = fw_sessions(node)
        return lambda: (not any(bgp(lab, s["node"], s["peer"], s["vrf"])[0] for s in ss), f"{len(ss)} FW session(s) down")

    def notest(node, desc):
        ss = sessions(lab, node, desc)
        return lambda: (not any(bgp(lab, s["node"], s["peer"], s["vrf"])[0] for s in ss), f"{len(ss)} session(s) {desc} down")

    def ping_ok(dst):
        return lambda: postcheck.ping(lab.name, lab.host, dst)

    def ping_fails(dst):
        return lambda: (lambda r: (not r[0], r[1]))(postcheck.ping(lab.name, lab.host, dst))

    def route(node, vrf, prefix, present=True):
        return lambda: (lambda r: (r[0] == present, r[1]))(lab.nodes[node].route(vrf, prefix))

    car_a_port = "isp"
    T = []
    T.append(dict(id=1, title=f"Carrier loss: {carriers[0]} then both carriers",
                  steps=[lambda: lab.linux_if(carriers[0], car_a_port, "down")],
                  checks=[("ISP1 carrier session down", notest(isp_routers[0], "ISP-A")),
                          ("ISP2 still has 0/0", route(isp_routers[1], "default", "0.0.0.0/0")),
                          ("Internet FW still has 0/0", route(inetfw, fw_vrf, "0.0.0.0/0")),
                          ("ping ISP-B host", ping_ok(b_host))],
                  then=[lambda: lab.linux_if(carriers[1], car_a_port, "down")],
                  then_checks=[("both ISP routers withdraw 0/0 to the FW (conditional default)", route(inetfw, fw_vrf, "0.0.0.0/0", present=False))],
                  restore=[lambda: lab.linux_if(carriers[0], car_a_port, "up"), lambda: lab.linux_if(carriers[1], car_a_port, "up")],
                  ping=b_host, outage_max=10))
    T.append(dict(id=2, title=f"ISP router down: {isp_routers[0]}",
                  steps=[lambda: lab.pause(isp_routers[0])],
                  checks=[("Internet FW session to ISP2 up", est(inetfw, isp_routers[1])),
                          ("Internet FW still has 0/0", route(inetfw, fw_vrf, "0.0.0.0/0")), ("ping ISP-B host", ping_ok(b_host))],
                  restore=[lambda: lab.pause(isp_routers[0], on=False)], ping=b_host, outage_max=10))
    T.append(dict(id=3, title=f"Spine down: {spines[0]}",
                  steps=[lambda: lab.pause(spines[0])],
                  checks=[(f"{leaf} EVPN session to {spines[1]} up", est(leaf, spines[1])),
                          (f"{leaf} still has {host_vrf} 0/0", route(leaf, host_vrf, "0.0.0.0/0")), ("ping ISP-A host", ping_ok(a_host))],
                  restore=[lambda: lab.pause(spines[0], on=False)], ping=a_host, outage_max=5))
    # test 4 (border / service leaf vPC peer death) removed: docker pause is a hung switch, not a power loss
    up = link_to(leaf, spines[0])
    T.append(dict(id=5, title=f"One leaf-spine link down: {leaf} {up[0] if up else '?'} -> {spines[0]}",
                  steps=[lambda: lab.port(leaf, up[0], "down")],
                  checks=[(f"{leaf} EVPN sessions to both spines up", est(leaf, "")), ("ping ISP-A host", ping_ok(a_host))],
                  restore=[lambda: lab.port(leaf, up[0], "up")], ping=a_host, outage_max=2))
    pl = lab.cfg_ports(bls[0], rf"channel-group {D['vpc_peer_link_port_channel']}\b")           # peer-link members, from the rendered config
    T.append(dict(id=6, title=f"vPC peer-link down ({bls[0]}-{bls[1]})",
                  steps=[(lambda p=p: lab.port(bls[0], p, "down")) for p in pl],
                  checks=[("ping ISP-A host", ping_ok(a_host))],
                  restore=[(lambda p=p: lab.port(bls[0], p, "up")) for p in pl], ping=a_host, outage_max=5))
    ka = (lab.cfg_ports(bls[0], r"vrf member " + re.escape(D["vpc_keepalive_vrf"])) + [None])[0]   # keepalive port, from the rendered config
    T.append(dict(id=7, title=f"vPC keepalive down ({bls[0]} {ka or 'no keepalive port found in the config'})",
                  steps=[lambda: lab.port(bls[0], ka, "down")] if ka else [],
                  checks=[("ping ISP-A host (no change expected)", ping_ok(a_host))],
                  restore=[lambda: lab.port(bls[0], ka, "up")] if ka else [], ping=a_host, outage_max=0.5))
    fwp = [p for p, fn, fp in lab.links(intfw)]
    T.append(dict(id=8, title=f"One FW port-channel member down: {intfw} {fwp[0] if fwp else '?'}",
                  steps=[lambda: lab.linux_if(intfw, fwp[0], "down")],
                  checks=[("Internal FW sessions up (LACP keeps the bundle)", est(intfw, "")), ("ping ISP-A host", ping_ok(a_host))],
                  restore=[lambda: lab.linux_if(intfw, fwp[0], "up")], ping=a_host, outage_max=2))
    T.append(dict(id=9, title=f"Silent failure toward the Internal FW (netem 100% loss on {intfw} ports)",
                  steps=[(lambda p=p: lab.netem(intfw, p)) for p in fwp],
                  checks=[(f"{sls[0]} sessions to {intfw} down by BFD", fw_down(sls[0]))],
                  restore=[(lambda p=p: lab.netem(intfw, p, on=False)) for p in fwp], ping=a_host, outage_max=None, bfd=True))
    T.append(dict(id=10, title=f"Internal FW stand-in down: {intfw}",
                  steps=[lambda: lab.pause(intfw)],
                  checks=[(f"{leaf} loses {host_vrf} 0/0 (single active unit in the lab)", route(leaf, host_vrf, "0.0.0.0/0", present=False)),
                          ("ping ISP-A host fails", ping_fails(a_host))],
                  restore=[lambda: lab.pause(intfw, on=False)], ping=None, outage_max=None))
    T.append(dict(id=11, title=f"Internet FW stand-in down: {inetfw}",
                  steps=[lambda: lab.pause(inetfw)],
                  checks=[(f"{bls[0]} TRANSIT 0/0 gone", route(bls[0], transit, "0.0.0.0/0", present=False)),
                          ("ping ISP-A host fails", ping_fails(a_host))],
                  restore=[lambda: lab.pause(inetfw, on=False)], ping=None, outage_max=None))
    T.append(dict(id=12, title=f"Filter test: {intfw} announces an extra prefix 10.99.0.0/16 in TRANSIT",
                  steps=[lambda: lab.vtysh(intfw, "configure terminal", f"ip route 10.99.0.0/16 blackhole vrf {fw_vrf}",
                                           f"router bgp {own_asn(lab, intfw)} vrf {fw_vrf}",
                                           "address-family ipv4 unicast", "redistribute static")],
                  checks=[(f"{sls[0]} rejects 10.99.0.0/16 in TRANSIT", route(sls[0], transit, "10.99.0.0/16", present=False))],
                  restore=[lambda: lab.vtysh(intfw, "configure terminal", f"no ip route 10.99.0.0/16 blackhole vrf {fw_vrf}")],
                  ping=None, outage_max=None))
    T.append(dict(id=13, title=f"BGP graceful restart: {leaf} BGP process restart, then {isp_routers[0]}",
                  steps=[lambda: f"{leaf} restart bgp (NX-API {postcheck.httpx.post(f'https://{lab.nodes[leaf].ip}/ins', auth=(postcheck.NXOS_USER, postcheck.NXOS_PASS), verify=False, timeout=60, headers={'content-type': 'application/json'}, content=json.dumps({'ins_api': {'version': '1.0', 'type': 'cli_show', 'chunk': '0', 'sid': '1', 'input': 'restart bgp ' + str(own_asn(lab, leaf)), 'output_format': 'json'}})).status_code})"],
                  checks=[(f"{leaf} EVPN sessions back", est(leaf, "")), ("ping ISP-A host", ping_ok(a_host))],
                  then=[lambda: (sh("docker", "exec", lab.ctr(isp_routers[0]), "sh", "-c", FRR_BGP_RESTART)
                                 if lab.nodes[isp_routers[0]].kind == "linux" else
                                 sh("docker", "exec", lab.ctr(isp_routers[0]), postcheck.XR_CLI, "process restart bgp"))[1][:80]],
                  then_checks=[("Internet FW sessions back", est(inetfw, ""))],
                  restore=[], ping=a_host, outage_max=1))
    T.append(dict(id=14, title=f"ISP1 returns: VRRP preempt after {preempt_s} s" + (" (FRR stand-in: no VRRP, same waits)" if lab.nodes[isp_routers[0]].kind == "linux" else ""),
                  steps=[lambda: lab.pause(isp_routers[0]), lambda: time.sleep(SETTLE_S), lambda: lab.pause(isp_routers[0], on=False)],
                  checks=[("ping ISP-B host right after return", ping_ok(b_host))],
                  then=[lambda: time.sleep(preempt_s + 30)],
                  then_checks=[("Internet FW sessions to both ISP routers", est(inetfw, ""))],
                  restore=[], ping=b_host, outage_max=5))
    T.append(dict(id=15, title=f"Node reload with its rendered config: {leaf}",
                  steps=[lambda: sh("timeout", "15", "docker", "exec", lab.ctr(leaf), "/.venv/bin/python", "-c",
                                    "import socket,time; s=socket.create_connection(('127.0.0.1',4000)); time.sleep(1); s.sendall(b'system_reset\\n'); time.sleep(1)")[1],
                         lambda: time.sleep(600)],
                  checks=[(f"{leaf} EVPN sessions back", est(leaf, "")), ("ping ISP-A host", ping_ok(a_host))],
                  restore=[], ping=None, outage_max=None))
    return T


def full_postcheck(site):
    rc, out = sh(sys.executable, os.path.join(HERE, "postcheck.py"), site, timeout=1800)
    return rc == 0, out.splitlines()[0] if out else ""


def run_test(lab, t, log):
    rec = {"id": t["id"], "title": t["title"], "steps": [], "checks": [], "pass": False}
    ok, line = full_postcheck(lab.site)
    rec["baseline"] = line
    if not ok:
        rec["error"] = "baseline post-check not clean: test not run"
        return rec
    pinger = Pinger(lab, t["ping"]) if t.get("ping") else None
    if pinger:
        pinger.start(); time.sleep(5)
    for _, fn in t["checks"] + t.get("then_checks", []):        # 8 Oct: record the up-before sessions
        if isinstance(fn, UpBefore):
            fn.prime()
    t_start = time.time()
    for s in t["steps"]:
        rec["steps"].append(str(s()))
    t_break = time.time()                                       # 8 Oct: the break is applied when its steps return
    time.sleep(SETTLE_S)
    for label, fn in t["checks"]:
        try:
            c_ok, got = fn()
        except Exception as e:
            c_ok, got = False, f"error {e}"
        rec["checks"].append({"check": label, "pass": c_ok, "got": got})
    for s in t.get("then", []):
        rec["steps"].append(str(s()))
    if t.get("then"):
        time.sleep(SETTLE_S)
    for label, fn in t.get("then_checks", []):
        try:
            c_ok, got = fn()
        except Exception as e:
            c_ok, got = False, f"error {e}"
        rec["checks"].append({"check": label, "pass": c_ok, "got": got})
    if pinger:
        pinger.stop_ = True; pinger.join(10)
        rec["outage_s"], open_ended = pinger.outage_since(t_break, time.time())
        rec["longest_gap_s"] = pinger.outage()
        if t.get("outage_max") is not None:
            ok_o = rec["outage_s"] is not None and rec["outage_s"] <= t["outage_max"]
            rec["checks"].append({"check": f"outage <= {t['outage_max']} s", "pass": ok_o,
                                  "got": open_ended or f"{rec['outage_s']} s (longest gap {rec['longest_gap_s']} s)"})
    for s in t["restore"]:
        rec["steps"].append(str(s()))
    time.sleep(RESTORE_S)
    ok, line = full_postcheck(lab.site)
    rec["after_restore"] = line
    rec["checks"].append({"check": "full post-check after restore", "pass": ok, "got": line})
    rec["pass"] = all(c["pass"] for c in rec["checks"])
    rec["seconds"] = round(time.time() - t_start)
    return rec


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("site")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--run", help="comma-separated test ids (default all)")
    ap.add_argument("--branch", help="Infrahub branch for the design values (default: the branch in the build's MANIFEST.json, else main)")
    a = ap.parse_args()
    lab = Lab(a.site)
    branch = a.branch
    if not branch:
        try:
            branch = json.load(open(os.path.join(lab.base, "MANIFEST.json"))).get("branch")
        except (OSError, ValueError):
            branch = None
    lab.design = load_design(branch or "main")
    lab.design["host_vrf"] = test_host_vrf(lab, branch or "main")
    tests = catalog(lab)
    if a.list:
        for t in tests:
            print(f"{t['id']:>2}  {t['title']}")
            for label, _ in t["checks"] + t.get("then_checks", []):
                print(f"      expect: {label}")
            if t.get("outage_max") is not None:
                print(f"      expect: outage <= {t['outage_max']} s (ping {t['ping']} every {PING_INTERVAL} s)")
            print("      expect: full post-check passes after restore")
        return
    ids = [int(x) for x in a.run.split(",")] if a.run else [t["id"] for t in tests]
    results, log = [], []
    for t in tests:
        if t["id"] in ids:
            print(f"== test {t['id']}: {t['title']} {time.strftime('%T')}", flush=True)
            r = run_test(lab, t, log)
            results.append(r)
            print(f"   {'PASS' if r['pass'] else 'FAIL'}  outage {r.get('outage_s')} s  " +
                  "; ".join(f"{'ok' if c['pass'] else 'X'} {c['check']}" for c in r["checks"]), flush=True)
            if r.get("error"):
                print("   ", r["error"]); break
    stamp = time.strftime("%Y%m%d-%H%M%S")
    os.makedirs(os.path.join(lab.base, "reports"), exist_ok=True)
    json.dump(results, open(os.path.join(lab.base, "reports", f"failtests-{stamp}.json"), "w"), indent=2)
    txt = "\n".join(f"{r['id']:>2} {'PASS' if r['pass'] else 'FAIL'}  outage {r.get('outage_s')} s  {r['title']}" for r in results)
    open(os.path.join(lab.base, "reports", f"failtests-{stamp}.txt"), "w").write(txt + "\n")
    print(txt)
    sys.exit(0 if results and all(r["pass"] for r in results) else 7)


if __name__ == "__main__":
    main()
