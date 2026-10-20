"""Phase 3 lab artifacts for one site, both built from query lab_site (P3-45, P3-48).

LabTopology  -> containerlab topology (YAML): the lab path nodes, every link between them found in Infrahub, plus the
                lab-only stand-ins (carriers, test hosts).
LabExpected  -> post-check expectations (JSON): every BGP session on the lab nodes (both ends), key routes per node and
                VRF, and the end-to-end pings. Nothing is typed by hand: sessions come from the attachments, loopbacks,
                links and carrier /30s, as in the configs.

Lab-only values below are Phase 3 decisions (claude/phase3-decisions.md, P3-30 .. P3-75).
"""
import ipaddress
import json
import re

from infrahub_sdk.transforms import InfrahubTransform

# ---------------------------------------------------------------- approved constants (Phase 3)
LAB_NODES = ("L11", "S1", "S2", "SL1", "SL2", "BL1", "BL2", "EDGE1", "EDGE2", "INTFW1", "INETFW1", "ISP1", "ISP2")  # P3-30..P3-37
NXOS_ROLES = {"leaf", "spine", "border_leaf", "service_leaf", "edge", "dmz"}
FW_ROLES = {"internal_fw", "internet_fw"}
VTEP_ROLES = {"leaf", "border_leaf", "service_leaf"}
N9KV_IMAGE = "vrnetlab/cisco_n9kv:10.6.4.M-lite"        # P3-36 / P3-71: NX-OS 10.6(4)M lite, built with vrnetlab
N9KV_ENV = {"QEMU_MEMORY": 8192, "QEMU_SMP": 3, "CLAB_MGMT_PASSTHROUGH": '"true"', "SCRAPLI_TIMEOUT": 3600}  # P3-36 (8 GB); P3-72 (3 vCPU); L-14: guest mgmt0 = container mgmt IP; 8 Oct: vrnetlab boot wait 3600 s
XRD_IMAGE = "ios-xr/xrd-control-plane:25.2.1"            # P3-37 / P3-71: IOS XRd Control Plane 25.2.1
ISP_LAB_STANDIN = "frr"                                  # I-04 (8 Oct): ISP routers in the lab: "frr" (stand-in, frr-lab-init) or "xrd"
FRR_IMAGE = "quay.io/frrouting/frr:10.6.0"               # P3-73
HOST_IMAGE = "alpine:3.22"                               # P3-42
LAB_NAME = "vamd-{site}-p3"                              # P3-74
MGMT_NETWORK = "vamd-{site}-mgmt"
LAB_MGMT_HOST = {"ISP-A": 240, "ISP-B": 241, "HOST-PROD": 245, "HOST-ISP-A": 246, "HOST-ISP-B": 247}  # P3-75: top of the mgmt /24
CARRIER_HOST_NET = {"ISP-A": "198.18.1.0/24", "ISP-B": "198.18.2.0/24"}   # P3-69 (corrects P3-41)
HOST_ADDR, HOST_GW = 10, 1                               # P3-40: host .10, gateway .1
FW_VRF_ID = {"TRANSIT": 1, "PROD": 10, "DEV": 11, "DC2-PROD": 12, "DC2-DEV": 13, "CITRIX": 15, "PARTNER": 16}  # FortiOS (PC3)
INETFW_TABLE_VRF = 1
FW_HOST, EDGE_FW_HOST = 3, 10
XRD_PORT_FROM = "FourHundredGigE0/0/0/"                  # P3-70: FH0/0/0/<2k> -> Gi0/0/0/<k>


def v(node, *path):
    for p in path:
        node = (node or {}).get(p)
    return node


def natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(s))]


class LabData:
    """The query response, indexed: devices, interfaces, attachments, site facts."""

    def __init__(self, data: dict):
        sites = data["LocationSite"]["edges"]
        if len(sites) != 1:
            raise ValueError(f"expected one site, got {len(sites)}")
        s = sites[0]["node"]
        self.site = s["name"]["value"]
        self.fabric_asn = v(s, "fabric_asn", "node", "asn", "value")
        self.owned = [e["node"]["prefix"]["value"] for e in s["owned_prefixes"]["edges"]]
        self.summaries = []          # (vrf, prefix) for this site, same rule as the service leaf and the firewalls
        for ve in sorted(s["vrfs"]["edges"], key=lambda e: e["node"]["name"]["value"]):
            vrf = ve["node"]
            for se in sorted(vrf["summaries"]["edges"], key=lambda e: e["node"]["prefix"]["value"]):
                own = v(se["node"], "site", "node", "name", "value")
                if vrf["multisite"]["value"] or own == self.site:
                    self.summaries.append((vrf["name"]["value"], se["node"]["prefix"]["value"]))
        self.devices = {}
        for e in data["DcimDevice"]["edges"]:
            d = e["node"]
            ifs = {}
            for ie in d["interfaces"]["edges"]:
                i = ie["node"]
                ifs[i["name"]["value"]] = {
                    "type": i["__typename"], "role": v(i, "role", "value"), "desc": v(i, "description", "value") or "",
                    "addrs": [a["node"]["address"]["value"] for a in (v(i, "ip_addresses") or {}).get("edges", [])],
                    "segment": v(i, "access_segment", "node"),
                }
            self.devices[d["name"]["value"]] = {"role": d["role"]["value"], "asn": v(d, "asn", "node", "asn", "value"), "ifs": ifs}
        self.attachments = [e["node"] for e in data["FabricAttachment"]["edges"]]
        self.provider_asn = {v(e["node"], "provider", "node", "name", "value"): e["node"]["asn"]["value"]
                             for e in data["FabricAutonomousSystem"]["edges"] if v(e["node"], "provider", "node")}
        self.lab = [f"{self.site}-{n}" for n in LAB_NODES]
        missing = [n for n in self.lab if n not in self.devices]
        if missing:
            raise ValueError(f"{self.site}: lab nodes missing from Infrahub: {missing}")

    def role(self, name):
        return self.devices[name]["role"]

    def addr(self, name, ifname):
        a = self.devices[name]["ifs"].get(ifname, {}).get("addrs") or []
        return a[0] if a else None

    def ip(self, name, ifname):
        a = self.addr(name, ifname)
        return str(ipaddress.ip_interface(a).ip) if a else None

    def mgmt(self, name):
        for ifname, i in self.devices[name]["ifs"].items():
            if i["role"] == "management" and i["addrs"]:
                return ipaddress.ip_interface(i["addrs"][0])
        raise ValueError(f"{name}: no management address")

    def carriers(self):
        """(isp router, upstream port, provider, our address/len) for each lab ISP router."""
        out = []
        for name in self.lab:
            if self.role(name) != "isp_router":
                continue
            for ifname, i in self.devices[name]["ifs"].items():
                if i["role"] == "upstream":
                    provider = next((p for p in self.provider_asn if i["desc"].startswith(p + " ")), None)
                    if provider is None:
                        raise ValueError(f"{name} {ifname}: no provider matches '{i['desc']}'")
                    out.append((name, ifname, provider, i["addrs"][0]))
        return sorted(out)

    def access_ports(self):
        out = []
        for name in self.lab:
            for ifname, i in sorted(self.devices[name]["ifs"].items(), key=lambda t: natural(t[0])):
                if i["role"] == "access" and i["segment"]:
                    out.append((name, ifname, i["segment"]))
        return out

    def public(self, plen):
        """The site's public block of one length: an owned prefix outside 10.0.0.0/8, /27 or shorter (the rule in
        internet_fw_config.j2). Not ipaddress.is_private: it counts the documentation ranges (198.51.100.0/24) as private."""
        for p in self.owned:
            n = ipaddress.ip_network(p)
            if not p.startswith("10.") and n.prefixlen <= 27 and n.prefixlen == plen:
                return str(n)
        return None


def endpoint(lab: LabData, device: str, ifname: str) -> str:
    role = lab.role(device)
    if role in NXOS_ROLES:
        m = re.fullmatch(r"Ethernet1/(\d+)", ifname)
        if not m:
            raise ValueError(f"{device} {ifname}: no containerlab name rule")
        return f"{device}:eth{m.group(1)}"
    if role == "isp_router":
        if not ifname.startswith(XRD_PORT_FROM):
            raise ValueError(f"{device} {ifname}: no XRd name rule")
        return f"{device}:Gi0-0-0-{int(ifname.rsplit('/', 1)[1]) // 2}"
    if role in FW_ROLES:
        return f"{device}:{ifname}"          # FortiOS port names are the Linux interface names (P3-62)
    raise ValueError(f"{device}: role {role} has no endpoint rule")


def build_links(lab: LabData) -> list[tuple[str, str]]:
    """Every link between two lab nodes: /31 core links by shared prefix, the rest by 'device port' descriptions
    that agree at both ends."""
    links = set()
    lab_set = set(lab.lab)
    by_net = {}
    for name in lab.lab:
        for ifname, i in lab.devices[name]["ifs"].items():
            if i["type"] != "InterfacePhysical":
                continue
            for a in i["addrs"]:
                net = ipaddress.ip_interface(a).network
                if net.prefixlen == 31:
                    by_net.setdefault(net, []).append((name, ifname))
    for net, ends in by_net.items():
        if len(ends) == 2 and ends[0][0] != ends[1][0]:
            links.add(tuple(sorted(ends)))
    for name in lab.lab:
        for ifname, i in lab.devices[name]["ifs"].items():
            if i["type"] != "InterfacePhysical":
                continue
            parts = i["desc"].split(" ")
            if len(parts) != 2 or parts[0] not in lab_set:
                continue
            far, far_if = parts
            back = lab.devices[far]["ifs"].get(far_if)
            if back and back["type"] == "InterfacePhysical" and back["desc"] == f"{name} {ifname}":
                links.add(tuple(sorted(((name, ifname), (far, far_if)))))
    return sorted(links, key=lambda t: (natural(t[0][0]), natural(t[0][1]), natural(t[1][0]), natural(t[1][1])))


def yq(s) -> str:
    return json.dumps(str(s))


class LabTopology(InfrahubTransform):
    query = "lab_site"

    async def transform(self, data: dict) -> str:
        lab = LabData(data)
        site = lab.site
        mgmt_net = None
        used = {}
        for name in lab.devices:
            try:
                m = lab.mgmt(name)
            except ValueError:
                continue
            used[str(m.ip)] = name
            if name in lab.lab:
                if mgmt_net is None:
                    mgmt_net = m.network
                elif m.network != mgmt_net:
                    raise ValueError(f"{name}: management {m} outside {mgmt_net}")
        for key, h in LAB_MGMT_HOST.items():
            ip = str(mgmt_net[h])
            if ip in used:
                raise ValueError(f"lab management address {ip} for {key} is already {used[ip]}'s")

        out = [f"# {site} Phase 3 lab topology for containerlab. Rendered by Infrahub (lab_topology), do not edit.",
               "# Startup configs: configs/<node>.cfg (NX-OS startup-config" + (", IOS XR iosxr-lab-config" if ISP_LAB_STANDIN == "xrd" else "") + " artifacts);",
               "# stand-in scripts: configs/<node>.sh (frr-lab-init artifacts" + ("" if ISP_LAB_STANDIN == "xrd" else ", ISP routers included") + "; carriers from the ISP routers' carrier-lab-init).",
               f"name: {LAB_NAME.format(site=site.lower())}",
               "mgmt:",
               f"  network: {MGMT_NETWORK.format(site=site.lower())}",
               f"  ipv4-subnet: {mgmt_net}",
               "topology:",
               "  nodes:"]

        def node(name, lines):
            out.append(f"    {name}:")
            out.extend(f"      {line}" for line in lines)

        for name in sorted(lab.lab, key=natural):
            role = lab.role(name)
            mip = lab.mgmt(name).ip
            if role in NXOS_ROLES:
                node(name, ["kind: cisco_n9kv", f"image: {N9KV_IMAGE}", f"mgmt-ipv4: {mip}", f"startup-config: configs/{name}.cfg",
                            "env:"] + [f"  {k}: {val}" for k, val in N9KV_ENV.items()])
            elif role == "isp_router" and ISP_LAB_STANDIN == "xrd":
                node(name, ["kind: cisco_xrd", f"image: {XRD_IMAGE}", f"mgmt-ipv4: {mip}", f"startup-config: configs/{name}.cfg"])
            elif role in FW_ROLES or role == "isp_router":
                node(name, ["kind: linux", f"image: {FRR_IMAGE}", f"mgmt-ipv4: {mip}", "binds:",
                            f"  - configs/{name}.sh:/lab/init.sh:ro", "exec:", "  - sh /lab/init.sh"])
            else:
                raise ValueError(f"{name}: role {role} has no node rule")
        for isp, port, provider, _ in lab.carriers():
            cname = f"{site}-{provider}"
            node(cname, ["kind: linux", f"image: {FRR_IMAGE}", f"mgmt-ipv4: {mgmt_net[LAB_MGMT_HOST[provider]]}", "binds:",
                         f"  - configs/{cname}.sh:/lab/init.sh:ro", "exec:", "  - sh /lab/init.sh"])
            hnet = ipaddress.ip_network(CARRIER_HOST_NET[provider])
            node(f"{site}-HOST-{provider}", ["kind: linux", f"image: {HOST_IMAGE}",
                                             f"mgmt-ipv4: {mgmt_net[LAB_MGMT_HOST['HOST-' + provider]]}", "exec:",
                                             f"  - ip addr add {hnet[HOST_ADDR]}/{hnet.prefixlen} dev eth1",
                                             "  - ip link set eth1 up",
                                             f"  - ip route replace default via {hnet[HOST_GW]} dev eth1"])
        for dev, ifname, seg in lab.access_ports():
            pnet = ipaddress.ip_network(seg["prefix"]["node"]["prefix"]["value"])
            node(f"{site}-HOST-PROD", ["kind: linux", f"image: {HOST_IMAGE}", f"mgmt-ipv4: {mgmt_net[LAB_MGMT_HOST['HOST-PROD']]}",
                                       "exec:", f"  - ip addr add {pnet[HOST_ADDR]}/{pnet.prefixlen} dev eth1",
                                       "  - ip link set eth1 up",
                                       f"  - ip route replace default via {pnet[HOST_GW]} dev eth1"])
        out.append("  links:")
        for (a, ai), (b, bi) in build_links(lab):
            out.append(f"    - endpoints: [{yq(endpoint(lab, a, ai))}, {yq(endpoint(lab, b, bi))}]")
        for isp, port, provider, _ in lab.carriers():
            out.append(f"    - endpoints: [{yq(endpoint(lab, isp, port))}, {yq(f'{site}-{provider}:isp')}]")
            out.append(f"    - endpoints: [{yq(f'{site}-{provider}:host')}, {yq(f'{site}-HOST-{provider}:eth1')}]")
        for dev, ifname, seg in lab.access_ports():
            out.append(f"    - endpoints: [{yq(endpoint(lab, dev, ifname))}, {yq(f'{site}-HOST-PROD:eth1')}]")
        return "\n".join(out) + "\n"


class LabExpected(InfrahubTransform):
    query = "lab_site"

    async def transform(self, data: dict) -> str:
        lab = LabData(data)
        site, fasn = lab.site, lab.fabric_asn
        lab_set = set(lab.lab)
        sessions = []

        def sess(node, vrf, peer, remote_as, desc, bfd=True, afi="ipv4 unicast"):
            sessions.append({"node": node, "vrf": vrf, "peer": peer, "remote_as": remote_as, "description": desc,
                             "afi": afi, "bfd": bfd, "state": "Established"})

        spines = sorted((n for n in lab.lab if lab.role(n) == "spine"), key=natural)
        for n in sorted(lab.lab, key=natural):
            if lab.role(n) in VTEP_ROLES:
                for s in spines:
                    sess(n, "default", lab.ip(s, "loopback0"), fasn, s, afi="l2vpn evpn")
                    sess(s, "default", lab.ip(n, "loopback0"), fasn, n, afi="l2vpn evpn")
        for a in sorted(lab.attachments, key=lambda a: a["vlan_id"]["value"]):
            leaves = sorted((e["node"]["name"]["value"] for e in a["switch_pair"]["node"]["members"]["edges"]), key=natural)
            fws = sorted((e["node"] for e in a["devices"]["edges"]), key=lambda d: natural(d["name"]["value"]))
            fw = next((d for d in fws if d["name"]["value"] in lab_set), None)
            if fw is None or not all(l in lab_set for l in leaves):
                continue
            net = ipaddress.ip_network(a["prefix"]["node"]["prefix"]["value"])
            vrf = a["vrf"]["node"]["name"]["value"]
            fw_vrf = f"vrf{INETFW_TABLE_VRF if lab.role(fw['name']['value']) == 'internet_fw' else FW_VRF_ID[vrf]}"
            for k, leaf in enumerate(leaves, start=1):
                sess(leaf, vrf, str(net[FW_HOST]), fw["asn"]["node"]["asn"]["value"], a["name"]["value"])
                sess(fw["name"]["value"], fw_vrf, str(net[k]), fasn, leaf)
        inetfw = next(n for n in lab.lab if lab.role(n) == "internet_fw")
        isps = sorted((n for n in lab.lab if lab.role(n) == "isp_router"), key=natural)
        edge_if = next(i for i in lab.devices[isps[0]]["ifs"] if i.startswith("Bundle-Ether") and "." in i)
        edge_net = ipaddress.ip_interface(lab.addr(isps[0], edge_if)).network
        inetfw_ip = str(edge_net[EDGE_FW_HOST])
        for r in isps:
            sess(inetfw, f"vrf{INETFW_TABLE_VRF}", lab.ip(r, edge_if), lab.devices[r]["asn"], r)
            sess(r, "default", inetfw_ip, lab.devices[inetfw]["asn"], inetfw[:-1])
        xl = {r: next(i for i, d in lab.devices[r]["ifs"].items() if d["role"] == "core") for r in isps}
        sess(isps[0], "default", lab.ip(isps[1], xl[isps[1]]), lab.devices[isps[1]]["asn"], isps[1])
        sess(isps[1], "default", lab.ip(isps[0], xl[isps[0]]), lab.devices[isps[0]]["asn"], isps[0])
        for isp, port, provider, ours in lab.carriers():
            net = ipaddress.ip_interface(ours).network
            carrier = f"{site}-{provider}"
            sess(isp, "default", str(net[1]), lab.provider_asn[provider], provider)
            sess(carrier, "default", str(ipaddress.ip_interface(ours).ip), lab.devices[isp]["asn"], isp)

        tenant_summ = sorted({p for vrf, p in lab.summaries if vrf != "TRANSIT"})
        p24 = lab.public(24)
        if p24 is None:
            raise ValueError(f"{site}: no public /24 among the site's owned prefixes")
        routes = []

        def route(node, vrf, prefix, why):
            routes.append({"node": node, "vrf": vrf, "prefix": prefix, "present": True, "why": why})

        access = lab.access_ports()
        for dev, ifname, seg in access:
            route(dev, seg["vrf"]["node"]["name"]["value"], "0.0.0.0/0", "default from the Internal FW via the service leaves (EVPN type-5)")
        for n in sorted(lab.lab, key=natural):
            role = lab.role(n)
            if role == "service_leaf":
                route(n, "TRANSIT", "0.0.0.0/0", "default from the border leaves (EVPN), sent on to the Internal FW")
                for vrf, p in lab.summaries:
                    if vrf != "TRANSIT":
                        route(n, vrf, "0.0.0.0/0", f"default from the Internal FW in {vrf}")
                        route(n, "TRANSIT", p, f"{vrf} summary leaked by the Internal FW into TRANSIT")
            elif role == "border_leaf":
                route(n, "TRANSIT", "0.0.0.0/0", "default from the Internet FW")
                for p in tenant_summ:
                    route(n, "TRANSIT", p, "tenant summary from the service leaves (EVPN), sent on to the Internet FW")
            elif role == "internal_fw":
                route(n, "vrf1", "0.0.0.0/0", "default from the service leaves in TRANSIT")
                for vrf, p in lab.summaries:
                    if vrf != "TRANSIT":
                        route(n, f"vrf{FW_VRF_ID[vrf]}", "0.0.0.0/0", "default leaked from TRANSIT")
                        route(n, "vrf1", p, f"{vrf} summary leaked from vrf{FW_VRF_ID[vrf]}")
            elif role == "internet_fw":
                route(n, f"vrf{INETFW_TABLE_VRF}", "0.0.0.0/0", "conditional default from the ISP routers")
                for p in tenant_summ:
                    route(n, f"vrf{INETFW_TABLE_VRF}", p, "tenant summary from the border leaves")
            elif role == "isp_router":
                route(n, "default", "0.0.0.0/0", "default from the carriers (rib-has-route condition)")
                route(n, "default", p24, "own public /24 (static Null0, network)")
                for prov, hn in sorted(CARRIER_HOST_NET.items()):
                    route(n, "default", hn, f"{prov} internet host subnet")
        for isp, port, provider, ours in lab.carriers():
            route(f"{site}-{provider}", "default", p24, f"{site} public /24 from {isp}")

        seen, uniq = set(), []
        for r in routes:
            key = (r["node"], r["vrf"], r["prefix"])
            if key not in seen:
                seen.add(key); uniq.append(r)
        routes = uniq
        pings = []
        for dev, ifname, seg in access:
            pnet = ipaddress.ip_network(seg["prefix"]["node"]["prefix"]["value"])
            pings.append({"from": f"{site}-HOST-PROD", "to": str(pnet[HOST_GW]), "why": "anycast gateway on the server leaf"})
            for prov, hn in sorted(CARRIER_HOST_NET.items()):
                hnet = ipaddress.ip_network(hn)
                pings.append({"from": f"{site}-HOST-PROD", "to": str(hnet[HOST_ADDR]),
                              "why": f"end to end via SL, Internal FW, TRANSIT, BL, Internet FW (NAT to {lab.public(25)}), {prov}"})
        doc = {"site": site, "source": "Infrahub lab_expected transform; do not edit",
               "lab_nodes": sorted(lab.lab, key=natural),
               "bgp_sessions": sessions, "routes": routes, "pings": pings}
        return json.dumps(doc, indent=2) + "\n"


# ================================================================ Option 5 (claude/option5-decisions.md): the entire site
# LabTopologyFull -> containerlab topology of every production device of the site (OOB excluded), the other site's DCI and BGW
# switches as FRR stand-ins (Multi-Site, control plane), and the lab-only outside nodes: carriers, internet hosts, the PROD
# host, one SD-WAN branch, the VPN partner, one cloud exchange per cloud router. Lab-only nodes carry the artifact that builds
# them as containerlab labels (vamd.artifact, vamd.target), read by labctl (D5-41).
FULL_LAB_NAME = "vamd-{site}-full"                                  # D5-05
FULL_MGMT_NETWORK = "vamd-{site}-full-mgmt"
FULL_NXOS = NXOS_ROLES | {"bgw", "dci"}                              # D5-01
FULL_FW = FW_ROLES | {"sdwan_fw", "vpn_fw"}
FULL_FRR = FULL_FW | {"isp_router", "cloud_router"}                 # D5-04
HA_PAIR_ROLES = FULL_FW                                             # D5-10: FortiGate A/P pairs; unit 2 runs with its data ports down
REMOTE_ROLES = ("bgw", "dci")                                       # D5-35: the other site's Multi-Site path
FULL_MGMT_HOST = {"BRANCH1": 242, "PARTNER": 243, "CX1": 248, "CX2": 249}   # D5-40
REMOTE_MGMT_FIRST = 250                                             # D5-40: the other site's stand-ins, .250 up
CARRIER_WAN = {"ISP-A": "partner", "ISP-B": "branch"}               # D5-31: which carrier's 'wan' port serves which node
XR_HU_PORT = re.compile(r"HundredGigE0/0/0/(\d+)")                  # D5-22: HundredGigE0/0/0/<n> -> Hu0-0-0-<n>


def full_endpoint(role: str, device: str, ifname: str) -> str:
    if role in FULL_NXOS:
        m = re.fullmatch(r"Ethernet1/(\d+)", ifname)
        if m:
            return f"{device}:eth{m.group(1)}"
    elif role in ("isp_router", "cloud_router"):
        if ifname.startswith(XRD_PORT_FROM):
            return f"{device}:Gi0-0-0-{int(ifname.rsplit('/', 1)[1]) // 2}"
        m = XR_HU_PORT.fullmatch(ifname)
        if m:
            return f"{device}:Hu0-0-0-{m.group(1)}"
    elif role in FULL_FW:
        return f"{device}:{ifname}"
    raise ValueError(f"{device} {ifname}: no containerlab name rule for role {role}")


def parse_device(d: dict) -> dict:
    ifs = {}
    for ie in d["interfaces"]["edges"]:
        i = ie["node"]
        ifs[i["name"]["value"]] = {
            "type": i["__typename"], "role": v(i, "role", "value"), "desc": v(i, "description", "value") or "",
            "addrs": [a["node"]["address"]["value"] for a in (v(i, "ip_addresses") or {}).get("edges", [])],
            "segment": v(i, "access_segment", "node"),
        }
    return {"role": d["role"]["value"], "asn": v(d, "asn", "node", "asn", "value"), "ifs": ifs,
            "site": v(d, "location", "node", "name", "value")}


class LabTopologyFull(InfrahubTransform):
    query = "lab_site_full"

    async def transform(self, data: dict) -> str:
        site = data["LocationSite"]["edges"][0]["node"]["name"]["value"]
        allsite = {e["node"]["name"]["value"]: parse_device(e["node"]) for e in data["DcimDevice"]["edges"]}
        local = {n: d for n, d in allsite.items() if d["role"] in FULL_ROLES_ALL}
        remote = {}
        for alias in ("RemoteBgws", "RemoteDcis"):
            for e in data[alias]["edges"]:
                d = parse_device(e["node"])
                if d["site"] and d["site"] != site:
                    remote[e["node"]["name"]["value"]] = d
        devs = {**local, **remote}
        names = set(devs)

        # management: production devices keep theirs; every lab-only and remote node from the top of the same /24
        mgmt_net, used, mgmt = None, {}, {}
        for n, d in allsite.items():
            for i in d["ifs"].values():
                if i["role"] == "management" and i["addrs"]:
                    ip = ipaddress.ip_interface(i["addrs"][0])
                    used[str(ip.ip)] = n
                    if n in local:
                        mgmt[n] = ip.ip
                        if mgmt_net is None:
                            mgmt_net = ip.network
                        elif ip.network != mgmt_net:
                            raise ValueError(f"{n}: management {ip} outside {mgmt_net}")
        missing = sorted(n for n in local if n not in mgmt)
        if missing:
            raise ValueError(f"{site}: no management address on {missing}")

        def lab_ip(host):
            ip = mgmt_net[host]
            if str(ip) in used:
                raise ValueError(f"lab management address {ip} is already {used[str(ip)]}'s")
            used[str(ip)] = "lab"
            return ip

        for k, n in enumerate(sorted(remote, key=natural)):
            mgmt[n] = lab_ip(REMOTE_MGMT_FIRST + k)

        # links between devices of the lab: /31 core links by shared prefix, the rest by descriptions that agree at both ends
        links, by_net = set(), {}
        for n in names:
            for ifname, i in devs[n]["ifs"].items():
                if i["type"] != "InterfacePhysical":
                    continue
                for a in i["addrs"]:
                    net = ipaddress.ip_interface(a).network
                    if net.prefixlen == 31:
                        by_net.setdefault(net, []).append((n, ifname))
        for net, ends in by_net.items():
            if len(ends) == 2 and ends[0][0] != ends[1][0]:
                links.add(tuple(sorted(ends)))
        for n in names:
            for ifname, i in devs[n]["ifs"].items():
                if i["type"] != "InterfacePhysical":
                    continue
                parts = i["desc"].split(" ")
                if parts[0] not in names or len(parts) > 2:
                    continue
                if len(parts) == 2:
                    far, far_if = parts
                    want = f"{n} {ifname}"
                else:                                                       # vPC server pair (L1/L2): "DC1-L2" on the same port number
                    far, far_if, want = parts[0], ifname, n
                back = devs[far]["ifs"].get(far_if)
                if back and back["type"] == "InterfacePhysical" and back["desc"] == want and (len(parts) == 2 or not any(
                        a["addrs"] for a in (i, back))):
                    links.add(tuple(sorted(((n, ifname), (far, far_if)))))
        links = sorted(links, key=lambda t: (natural(t[0][0]), natural(t[0][1]), natural(t[1][0]), natural(t[1][1])))
        ep = {}
        for (a, ai), (b, bi) in links:
            ep[(a, ai)] = full_endpoint(devs[a]["role"], a, ai)
            ep[(b, bi)] = full_endpoint(devs[b]["role"], b, bi)
        cabled = {}
        for (a, ai), (b, bi) in links:
            cabled.setdefault(a, set()).add(ep[(a, ai)].split(":", 1)[1])
            cabled.setdefault(b, set()).add(ep[(b, bi)].split(":", 1)[1])

        out = [f"# {site} entire-site lab topology for containerlab (option 5). Rendered by Infrahub (lab_topology_full), do not edit.",
               "# Every production device of the site (OOB excluded), the other site's DCI and BGW switches as FRR stand-ins,",
               "# and the lab-only outside nodes. Node labels vamd.artifact / vamd.target name the artifact behind each init script.",
               f"name: {FULL_LAB_NAME.format(site=site.lower())}",
               "mgmt:",
               f"  network: {FULL_MGMT_NETWORK.format(site=site.lower())}",
               f"  ipv4-subnet: {mgmt_net}",
               "topology:",
               "  nodes:"]

        def node(name, lines):
            out.append(f"    {name}:")
            out.extend(f"      {line}" for line in lines)

        def frr(name, ip, artifact, target, extra_exec=()):
            node(name, ["kind: linux", f"image: {FRR_IMAGE}", f"mgmt-ipv4: {ip}", "labels:", f"  vamd.artifact: {artifact}",
                        f"  vamd.target: {target}", "binds:", f"  - configs/{name}.sh:/lab/init.sh:ro", "exec:", "  - sh /lab/init.sh"]
                 + [f"  - {yq(x)}" for x in extra_exec])

        def host(name, ip, net, gw_host=HOST_GW):
            pnet = ipaddress.ip_network(net)
            node(name, ["kind: linux", f"image: {HOST_IMAGE}", f"mgmt-ipv4: {ip}", "exec:",
                        f"  - ip addr add {pnet[HOST_ADDR]}/{pnet.prefixlen} dev eth1", "  - ip link set eth1 up",
                        f"  - ip route replace default via {pnet[gw_host]} dev eth1"])

        for n in sorted(local, key=natural):
            role = local[n]["role"]
            if role in FULL_NXOS:
                node(n, ["kind: cisco_n9kv", f"image: {N9KV_IMAGE}", f"mgmt-ipv4: {mgmt[n]}", f"startup-config: configs/{n}.cfg",
                         "env:"] + [f"  {k}: {val}" for k, val in N9KV_ENV.items()])
            else:
                standby = role in HA_PAIR_ROLES and n.endswith("2")
                down = [f"ip link set {p} down" for p in sorted(cabled.get(n, ()), key=natural)] if standby else []
                frr(n, mgmt[n], "frr-lab-init", n, down)
        for n in sorted(remote, key=natural):
            frr(n, mgmt[n], "frr-lab-init", n)

        extra_links = []
        # carriers and their internet hosts (as the lab path), one per ISP router upstream port
        isps = sorted((n for n in local if local[n]["role"] == "isp_router"), key=natural)
        provider_asn = {v(e["node"], "provider", "node", "name", "value"): e["node"]["asn"]["value"]
                        for e in data["FabricAutonomousSystem"]["edges"] if v(e["node"], "provider", "node")}
        carriers = {}
        for isp in isps:
            for ifname, i in sorted(local[isp]["ifs"].items()):
                if i["role"] != "upstream":
                    continue
                provider = next((p for p in provider_asn if i["desc"].startswith(p + " ")), None)
                if provider is None:
                    raise ValueError(f"{isp} {ifname}: no provider matches '{i['desc']}'")
                cname = f"{site}-{provider}"
                carriers[provider] = cname
                frr(cname, lab_ip(LAB_MGMT_HOST[provider]), "carrier-lab-init", isp)
                host(f"{site}-HOST-{provider}", lab_ip(LAB_MGMT_HOST["HOST-" + provider]), CARRIER_HOST_NET[provider])
                extra_links += [(full_endpoint("isp_router", isp, ifname), f"{cname}:isp"), (f"{cname}:host", f"{site}-HOST-{provider}:eth1")]
        # the PROD test host (the site's first access port, D5-36)
        access = sorted(((n, ifname, i["segment"]) for n in local for ifname, i in local[n]["ifs"].items()
                         if i["role"] == "access" and i["segment"]), key=lambda t: (natural(t[0]), natural(t[1])))
        if access:
            dev, ifname, seg = access[0]
            host(f"{site}-HOST-PROD", lab_ip(LAB_MGMT_HOST["HOST-PROD"]), seg["prefix"]["node"]["prefix"]["value"])
            extra_links.append((full_endpoint(local[dev]["role"], dev, ifname), f"{site}-HOST-PROD:eth1"))
        # SD-WAN branch behind its carrier (D5-32)
        sdwan = sorted((n for n in local if local[n]["role"] == "sdwan_fw"), key=natural)
        wan_of = {use: prov for prov, use in CARRIER_WAN.items()}
        if sdwan and wan_of["branch"] in carriers:
            bname = f"{site}-BRANCH1"
            frr(bname, lab_ip(FULL_MGMT_HOST["BRANCH1"]), "branch-lab-init", sdwan[0])
            extra_links.append((f"{carriers[wan_of['branch']]}:wan", f"{bname}:wan"))
        # VPN partner behind its carrier (D5-33): the site's first partner with a translated prefix
        vpn = sorted((n for n in local if local[n]["role"] == "vpn_fw"), key=natural)
        partners = sorted(e["node"]["name"]["value"] for e in data["Partners"]["edges"]
                          if any(v(t["node"], "site", "node", "name", "value") == site for t in e["node"]["translated_prefixes"]["edges"]))
        if vpn and partners and wan_of["partner"] in carriers:
            pname = f"{site}-{partners[0]}"
            frr(pname, lab_ip(FULL_MGMT_HOST["PARTNER"]), "partner-lab-init", vpn[0])
            extra_links.append((f"{carriers[wan_of['partner']]}:wan", f"{pname}:wan"))
        # one cloud exchange per cloud router, on its cloud-exchange port (D5-34)
        for n in sorted((n for n in local if local[n]["role"] == "cloud_router"), key=natural):
            port = next(ifn for ifn, i in sorted(local[n]["ifs"].items()) if i["type"] == "InterfacePhysical" and i["role"] == "cloud_handoff")
            cx = f"{site}-CX{n[-1]}"
            frr(cx, lab_ip(FULL_MGMT_HOST[f"CX{n[-1]}"]), "cx-lab-init", n)
            extra_links.append((full_endpoint("cloud_router", n, port), f"{cx}:cr"))

        out.append("  links:")
        for (a, ai), (b, bi) in links:
            out.append(f"    - endpoints: [{yq(ep[(a, ai)])}, {yq(ep[(b, bi)])}]")
        for a, b in extra_links:
            out.append(f"    - endpoints: [{yq(a)}, {yq(b)}]")
        return "\n".join(out) + "\n"


FULL_ROLES_ALL = FULL_NXOS | FULL_FRR
