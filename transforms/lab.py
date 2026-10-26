"""Lab artifacts for one site, both built from query lab_site.

LabTopology  -> containerlab topology (YAML): the lab path nodes, every link between them found in Infrahub, plus the
                lab-only stand-ins (carriers, test hosts).
LabExpected  -> post-check expectations (JSON): every BGP session on the lab nodes (both ends), key routes per node and
                VRF, and the end-to-end pings. Nothing is typed by hand: sessions come from the attachments, loopbacks,
                links and carrier /30s, as in the configs.

The lab-only values below are set here, not in Infrahub.
"""
import ipaddress
import json
import re

from infrahub_sdk.transforms import InfrahubTransform

# ---------------------------------------------------------------- lab constants
LAB_NODES = ("L11", "L12", "S1", "S2", "SL1", "SL2", "BL1", "BL2", "EDGE1", "EDGE2", "INTFW1", "INETFW1", "ISP1", "ISP2")
# Role sets, VTEP roles, host indexes, VRF ids: design values read from Infrahub at the start of every transform (configure()).
N9KV_IMAGE = "vrnetlab/cisco_n9kv:10.6.4.M-lite"        # NX-OS 10.6(4)M lite, built with vrnetlab
N9KV_ENV = {"QEMU_MEMORY": 8192, "QEMU_SMP": 3, "CLAB_MGMT_PASSTHROUGH": '"true"', "SCRAPLI_TIMEOUT": 3600}  # 8 GB, 3 vCPU; guest mgmt0 = container mgmt IP; vrnetlab boot wait 3600 s
XRD_IMAGE = "ios-xr/xrd-control-plane:25.2.1"            # IOS XRd Control Plane 25.2.1
ISP_LAB_STANDIN = "frr"                                  # I-04 (8 Oct): ISP routers in the lab: "frr" (stand-in, frr-lab-init) or "xrd"
FRR_IMAGE = "quay.io/frrouting/frr:10.6.0"
HOST_IMAGE = "alpine:3.22"
LAB_NAME = "vamd-{site}-p3"
MGMT_NETWORK = "vamd-{site}-mgmt"
LAB_MGMT_HOST = {"ISP-A": 240, "ISP-B": 241, "HOST-PROD": 245, "HOST-ISP-A": 246, "HOST-ISP-B": 247,
                 "HOST-PROD2": 238, "HOST-PROD3": 239}  # top of the mgmt /24; extra hosts grow downward
CARRIER_HOST_NET = {"ISP-A": "198.18.1.0/24", "ISP-B": "198.18.2.0/24"}
HOST_ADDR, HOST_GW = 10, 1                               # host .10, carrier-side gateway .1 (the segment gateway is FabricDesign)


def prod_hosts(access):
    """One test host per access port - its own node name, mgmt address and in-segment address.

    Two access ports can share a segment (L11 and L12 both carry VLAN 1010), which is the point: hosts in one
    subnet on different leaves prove EVPN bridging between leaves. They cannot share an address, so the
    n-th host of a segment takes HOST_ADDR + n.
    """
    seen, out = {}, []
    for idx, (dev, ifname, seg) in enumerate(access):
        prefix = seg["prefix"]["node"]["prefix"]["value"]
        n = seen.get(prefix, 0)
        seen[prefix] = n + 1
        key = "HOST-PROD" if idx == 0 else f"HOST-PROD{idx + 1}"
        out.append({"key": key, "dev": dev, "ifname": ifname, "seg": seg,
                    "prefix": prefix, "addr_host": HOST_ADDR + n})
    return out
XRD_PORT_FROM = "FourHundredGigE0/0/0/"                  # FH0/0/0/<2k> -> Gi0/0/0/<k>


def v(node, *path):
    for p in path:
        node = (node or {}).get(p)
    return node


# ---------------------------------------------------------------- design values (FabricDesign)
PLATFORM_ROLES = ("spine", "leaf", "border_leaf", "service_leaf", "bgw", "dci", "edge", "dmz",
                  "internal_fw", "internet_fw", "sdwan_fw", "vpn_fw", "isp_router", "cloud_router")   # roles with a <role>_platform field
NXOS, FORTIOS, IOSXR = "Cisco NX-OS", "Fortinet FortiOS", "Cisco IOS XR"                          # DcimPlatform names (identifiers)
LAB_SITE_FIELDS = ("vtep_roles", "segment_gateway_host", "attachment_device_host", "edge_vlan_hosts", "p2p_link_prefix_length",
                   "attachment_leaf_hosts", "transit_vrf")
LAB_SITE_FULL_FIELDS = ("segment_gateway_host", "p2p_link_prefix_length")
# lab_expected: BFD flags per session type, carrier-side host, ISP edge sub-interface parts; lab_topology_full: FW HA priority
LAB_EXPECTED_FIELDS = ("overlay_bgp_bfd", "attachment_bgp_bfd", "carrier_side_host", "isp_bundle_id", "edge_vlan")
LAB_FULL_STANDBY_FIELDS = ("fw_ha_priority",)
DESIGN: dict = {}            # filled by configure() at the start of every transform(): field values plus the derived role sets


def design_node(data: dict) -> dict:
    fd = data.get("FabricDesign") or {}
    edges = fd.get("edges") or []
    if fd.get("count") != 1 or len(edges) != 1:
        raise ValueError(f"FabricDesign: expected exactly 1 object, found {fd.get('count', len(edges))}; fix it in Infrahub "
                         "(and check that the query asks for FabricDesign)")
    return edges[0]["node"]


def need(design: dict, field: str):
    value = (design.get(field) or {}).get("value")
    if value is None:
        raise ValueError(f"FabricDesign.{field} is empty; fix it in Infrahub")
    return value


def configure(data: dict, fields) -> None:
    """Read the FabricDesign fields this transform uses; derive the role sets from the role -> platform fields."""
    d = design_node(data)
    DESIGN.clear()
    DESIGN.update({f: need(d, f) for f in fields})
    platform = {r: need(d, f"{r}_platform") for r in PLATFORM_ROLES}
    DESIGN["nxos_roles"] = {r for r, p in platform.items() if p == NXOS}
    DESIGN["fw_roles"] = {r for r, p in platform.items() if p == FORTIOS}
    DESIGN["xr_roles"] = {r for r, p in platform.items() if p == IOSXR}


def natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(s))]


def isp_edge_subif(lab, isp: str) -> str:
    """The ISP router's edge-VLAN sub-interface Bundle-Ether<isp_bundle_id>.<edge_vlan> (generators/fabric_site.py naming)."""
    name = f"Bundle-Ether{DESIGN['isp_bundle_id']}.{DESIGN['edge_vlan']}"
    if name not in lab.devices[isp]["ifs"]:
        raise ValueError(f"{isp}: no edge sub-interface {name} (FabricDesign isp_bundle_id, edge_vlan)")
    return name


def fw_standby(role: str, name: str) -> bool:
    """A/P FortiGate pair: the unit with the lower FabricDesign.fw_ha_priority runs as standby (unit = trailing number)."""
    if role not in DESIGN["fw_roles"]:
        return False
    prio = {int(k): p for k, p in DESIGN["fw_ha_priority"].items()}
    m = re.search(r"(\d+)$", name)
    if not m or int(m.group(1)) not in prio:
        raise ValueError(f"{name}: no FabricDesign.fw_ha_priority entry for its unit number")
    return prio[int(m.group(1))] < max(prio.values())


class LabData:
    """The query response, indexed: devices, interfaces, attachments, site facts."""

    def __init__(self, data: dict):
        sites = data["LocationSite"]["edges"]
        if len(sites) != 1:
            raise ValueError(f"expected one site, got {len(sites)}")
        s = sites[0]["node"]
        self.site = s["name"]["value"]
        self.fabric_asn = v(s, "fabric_asn", "node", "asn", "value")
        self.owned = [(e["node"]["prefix"]["value"], v(e["node"], "role", "value")) for e in s["owned_prefixes"]["edges"]]
        self.vrf_id = {ve["node"]["name"]["value"]: v(ve["node"], "fortios_vrf_id", "value") for ve in s["vrfs"]["edges"]}
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

    def public(self, role):
        """The site's owned prefix with this IpamPrefix role (public_block, public_nat_pool, public_services)."""
        for p, r in self.owned:
            if r == role:
                return str(ipaddress.ip_network(p))
        return None

    def fw_vrf(self, vrf):
        """FortiOS VRF id of a VRF (FabricVRF.fortios_vrf_id)."""
        vid = self.vrf_id.get(vrf)
        if vid is None:
            raise ValueError(f"FabricVRF {vrf}: fortios_vrf_id is empty or the VRF is not in the site; fix it in Infrahub")
        return vid


def endpoint(lab: LabData, device: str, ifname: str) -> str:
    role = lab.role(device)
    if role in DESIGN["nxos_roles"]:
        m = re.fullmatch(r"Ethernet1/(\d+)", ifname)
        if not m:
            raise ValueError(f"{device} {ifname}: no containerlab name rule")
        return f"{device}:eth{m.group(1)}"
    if role == "isp_router":
        if not ifname.startswith(XRD_PORT_FROM):
            raise ValueError(f"{device} {ifname}: no XRd name rule")
        return f"{device}:Gi0-0-0-{int(ifname.rsplit('/', 1)[1]) // 2}"
    if role in DESIGN["fw_roles"]:
        return f"{device}:{ifname}"          # FortiOS port names are the Linux interface names
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
                if net.prefixlen == DESIGN["p2p_link_prefix_length"]:
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
        configure(data, LAB_SITE_FIELDS)
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

        out = [f"# {site} lab topology for containerlab. Rendered by Infrahub (lab_topology), do not edit.",
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
            if role in DESIGN["nxos_roles"]:
                node(name, ["kind: cisco_n9kv", f"image: {N9KV_IMAGE}", f"mgmt-ipv4: {mip}", f"startup-config: configs/{name}.cfg",
                            "env:"] + [f"  {k}: {val}" for k, val in N9KV_ENV.items()])
            elif role == "isp_router" and ISP_LAB_STANDIN == "xrd":
                node(name, ["kind: cisco_xrd", f"image: {XRD_IMAGE}", f"mgmt-ipv4: {mip}", f"startup-config: configs/{name}.cfg"])
            elif role in DESIGN["fw_roles"] or role == "isp_router":
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
        for h in prod_hosts(lab.access_ports()):
            pnet = ipaddress.ip_network(h["prefix"])
            node(f"{site}-{h['key']}", ["kind: linux", f"image: {HOST_IMAGE}", f"mgmt-ipv4: {mgmt_net[LAB_MGMT_HOST[h['key']]]}",
                                        "exec:", f"  - ip addr add {pnet[h['addr_host']]}/{pnet.prefixlen} dev eth1",
                                        "  - ip link set eth1 up",
                                        f"  - ip route replace default via {pnet[DESIGN['segment_gateway_host']]} dev eth1"])
        out.append("  links:")
        for (a, ai), (b, bi) in build_links(lab):
            out.append(f"    - endpoints: [{yq(endpoint(lab, a, ai))}, {yq(endpoint(lab, b, bi))}]")
        for isp, port, provider, _ in lab.carriers():
            out.append(f"    - endpoints: [{yq(endpoint(lab, isp, port))}, {yq(f'{site}-{provider}:isp')}]")
            out.append(f"    - endpoints: [{yq(f'{site}-{provider}:host')}, {yq(f'{site}-HOST-{provider}:eth1')}]")
        for h in prod_hosts(lab.access_ports()):
            hn = f"{site}-{h['key']}:eth1"
            out.append(f"    - endpoints: [{yq(endpoint(lab, h['dev'], h['ifname']))}, {yq(hn)}]")
        return "\n".join(out) + "\n"


class LabExpected(InfrahubTransform):
    query = "lab_site"

    async def transform(self, data: dict) -> str:
        configure(data, LAB_SITE_FIELDS + LAB_EXPECTED_FIELDS)
        lab = LabData(data)
        site, fasn = lab.site, lab.fabric_asn
        transit = DESIGN["transit_vrf"]
        fw_host, leaf_hosts = DESIGN["attachment_device_host"], DESIGN["attachment_leaf_hosts"]
        lab_set = set(lab.lab)
        sessions = []

        def sess(node, vrf, peer, remote_as, desc, bfd, afi="ipv4 unicast"):
            sessions.append({"node": node, "vrf": vrf, "peer": peer, "remote_as": remote_as, "description": desc,
                             "afi": afi, "bfd": bfd, "state": "Established"})

        spines = sorted((n for n in lab.lab if lab.role(n) == "spine"), key=natural)
        for n in sorted(lab.lab, key=natural):
            if lab.role(n) in DESIGN["vtep_roles"]:
                for s in spines:
                    sess(n, "default", lab.ip(s, "loopback0"), fasn, s, DESIGN["overlay_bgp_bfd"], afi="l2vpn evpn")
                    sess(s, "default", lab.ip(n, "loopback0"), fasn, n, DESIGN["overlay_bgp_bfd"], afi="l2vpn evpn")
        for a in sorted(lab.attachments, key=lambda a: a["vlan_id"]["value"]):
            leaves = sorted((e["node"]["name"]["value"] for e in a["switch_pair"]["node"]["members"]["edges"]), key=natural)
            fws = sorted((e["node"] for e in a["devices"]["edges"]), key=lambda d: natural(d["name"]["value"]))
            fw = next((d for d in fws if d["name"]["value"] in lab_set), None)
            if fw is None or not all(l in lab_set for l in leaves):
                continue
            net = ipaddress.ip_network(a["prefix"]["node"]["prefix"]["value"])
            vrf = a["vrf"]["node"]["name"]["value"]
            fw_vrf = f"vrf{lab.fw_vrf(transit) if lab.role(fw['name']['value']) == 'internet_fw' else lab.fw_vrf(vrf)}"
            for k, leaf in enumerate(leaves):
                sess(leaf, vrf, str(net[fw_host]), fw["asn"]["node"]["asn"]["value"], a["name"]["value"], DESIGN["attachment_bgp_bfd"])
                sess(fw["name"]["value"], fw_vrf, str(net[leaf_hosts[k]]), fasn, leaf, DESIGN["attachment_bgp_bfd"])
        inetfw = next(n for n in lab.lab if lab.role(n) == "internet_fw")
        isps = sorted((n for n in lab.lab if lab.role(n) == "isp_router"), key=natural)
        edge_if = isp_edge_subif(lab, isps[0])
        edge_net = ipaddress.ip_interface(lab.addr(isps[0], edge_if)).network
        inetfw_ip = str(edge_net[DESIGN["edge_vlan_hosts"]["INETFW"]])
        inetfw_vrf = f"vrf{lab.fw_vrf(transit)}"
        # edge, ISP cross-link and carrier sessions: the lab templates always render BFD on them (bfd=True)
        for r in isps:
            sess(inetfw, inetfw_vrf, lab.ip(r, edge_if), lab.devices[r]["asn"], r, True)
            sess(r, "default", inetfw_ip, lab.devices[inetfw]["asn"], inetfw[:-1], True)
        xl = {r: next(i for i, d in lab.devices[r]["ifs"].items() if d["role"] == "core") for r in isps}
        sess(isps[0], "default", lab.ip(isps[1], xl[isps[1]]), lab.devices[isps[1]]["asn"], isps[1], True)
        sess(isps[1], "default", lab.ip(isps[0], xl[isps[0]]), lab.devices[isps[0]]["asn"], isps[0], True)
        for isp, port, provider, ours in lab.carriers():
            net = ipaddress.ip_interface(ours).network
            carrier = f"{site}-{provider}"
            sess(isp, "default", str(net[DESIGN["carrier_side_host"]]), lab.provider_asn[provider], provider, True)
            sess(carrier, "default", str(ipaddress.ip_interface(ours).ip), lab.devices[isp]["asn"], isp, True)

        tenant_summ = sorted({p for vrf, p in lab.summaries if vrf != transit})
        p24 = lab.public("public_block")
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
                route(n, transit, "0.0.0.0/0", "default from the border leaves (EVPN), sent on to the Internal FW")
                for vrf, p in lab.summaries:
                    if vrf != transit:
                        route(n, vrf, "0.0.0.0/0", f"default from the Internal FW in {vrf}")
                        route(n, transit, p, f"{vrf} summary leaked by the Internal FW into TRANSIT")
            elif role == "border_leaf":
                route(n, transit, "0.0.0.0/0", "default from the Internet FW")
                for p in tenant_summ:
                    route(n, transit, p, "tenant summary from the service leaves (EVPN), sent on to the Internet FW")
            elif role == "internal_fw":
                route(n, f"vrf{lab.fw_vrf(transit)}", "0.0.0.0/0", "default from the service leaves in TRANSIT")
                for vrf, p in lab.summaries:
                    if vrf != transit:
                        route(n, f"vrf{lab.fw_vrf(vrf)}", "0.0.0.0/0", "default leaked from TRANSIT")
                        route(n, f"vrf{lab.fw_vrf(transit)}", p, f"{vrf} summary leaked from vrf{lab.fw_vrf(vrf)}")
            elif role == "internet_fw":
                route(n, inetfw_vrf, "0.0.0.0/0", "conditional default from the ISP routers")
                for p in tenant_summ:
                    route(n, inetfw_vrf, p, "tenant summary from the border leaves")
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
        hosts = prod_hosts(access)
        for h in hosts:
            pnet = ipaddress.ip_network(h["prefix"])
            name = f"{site}-{h['key']}"
            pings.append({"from": name, "to": str(pnet[DESIGN["segment_gateway_host"]]), "why": "anycast gateway on the server leaf"})
            for prov, hn in sorted(CARRIER_HOST_NET.items()):
                hnet = ipaddress.ip_network(hn)
                pings.append({"from": name, "to": str(hnet[HOST_ADDR]),
                              "why": f"end to end via SL, Internal FW, TRANSIT, BL, Internet FW (NAT to {lab.public('public_nat_pool')}), {prov}"})
        # the reason the extra hosts exist
        for i, a in enumerate(hosts):
            for b in hosts[i + 1:]:
                anet, bnet = ipaddress.ip_network(a["prefix"]), ipaddress.ip_network(b["prefix"])
                same = a["prefix"] == b["prefix"]
                why = ("host to host in one subnet on different leaves: EVPN bridging between leaves" if same
                       else "host to host across subnets through the anycast gateways: EVPN routing")
                if same and a["dev"] == b["dev"]:
                    continue
                pings.append({"from": f"{site}-{a['key']}", "to": str(bnet[b["addr_host"]]), "why": why})
        doc = {"site": site, "source": "Infrahub lab_expected transform; do not edit",
               "lab_nodes": sorted(lab.lab, key=natural),
               "node_asn": {n: lab.devices[n]["asn"] for n in sorted(lab.lab, key=natural) if lab.devices[n]["asn"] is not None},  # own AS (DcimDevice.asn)
               "bgp_sessions": sessions, "routes": routes, "pings": pings}
        return json.dumps(doc, indent=2) + "\n"


# ================================================================ Option 5: the entire site
# LabTopologyFull -> containerlab topology of every production device of the site (OOB excluded), the other site's DCI and BGW
# switches as FRR stand-ins (Multi-Site, control plane), and the lab-only outside nodes: carriers, internet hosts, the PROD
# host, one SD-WAN branch, the VPN partner, one cloud exchange per cloud router. Lab-only nodes carry the artifact that builds
# them as containerlab labels (vamd.artifact, vamd.target), read by labctl.
FULL_LAB_NAME = "vamd-{site}-full"
FULL_MGMT_NETWORK = "vamd-{site}-full-mgmt"
# every NX-OS role, FortiOS and IOS XR roles as FRR stand-ins, FortiGate A/P pairs (unit 2 runs with its data
# ports down): role sets from the role -> platform fields, see configure()
REMOTE_ROLES = ("bgw", "dci")                                       # the other site's Multi-Site path
FULL_MGMT_HOST = {"BRANCH1": 242, "PARTNER": 243, "CX1": 248, "CX2": 249}
REMOTE_MGMT_FIRST = 250                                             # the other site's stand-ins, .250 up
CARRIER_WAN = {"ISP-A": "partner", "ISP-B": "branch"}               # which carrier's 'wan' port serves which node
XR_HU_PORT = re.compile(r"HundredGigE0/0/0/(\d+)")                  # HundredGigE0/0/0/<n> -> Hu0-0-0-<n>


def full_endpoint(role: str, device: str, ifname: str) -> str:
    if role in DESIGN["nxos_roles"]:
        m = re.fullmatch(r"Ethernet1/(\d+)", ifname)
        if m:
            return f"{device}:eth{m.group(1)}"
    elif role in DESIGN["xr_roles"]:
        if ifname.startswith(XRD_PORT_FROM):
            return f"{device}:Gi0-0-0-{int(ifname.rsplit('/', 1)[1]) // 2}"
        m = XR_HU_PORT.fullmatch(ifname)
        if m:
            return f"{device}:Hu0-0-0-{m.group(1)}"
    elif role in DESIGN["fw_roles"]:
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
        configure(data, LAB_SITE_FULL_FIELDS + LAB_FULL_STANDBY_FIELDS)
        full_roles_all = DESIGN["nxos_roles"] | DESIGN["fw_roles"] | DESIGN["xr_roles"]
        site = data["LocationSite"]["edges"][0]["node"]["name"]["value"]
        allsite = {e["node"]["name"]["value"]: parse_device(e["node"]) for e in data["DcimDevice"]["edges"]}
        local = {n: d for n, d in allsite.items() if d["role"] in full_roles_all}
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
                    if net.prefixlen == DESIGN["p2p_link_prefix_length"]:
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

        def host(name, ip, net, gw_host=HOST_GW, addr_host=HOST_ADDR):
            pnet = ipaddress.ip_network(net)
            node(name, ["kind: linux", f"image: {HOST_IMAGE}", f"mgmt-ipv4: {ip}", "exec:",
                        f"  - ip addr add {pnet[addr_host]}/{pnet.prefixlen} dev eth1", "  - ip link set eth1 up",
                        f"  - ip route replace default via {pnet[gw_host]} dev eth1"])

        for n in sorted(local, key=natural):
            role = local[n]["role"]
            if role in DESIGN["nxos_roles"]:
                node(n, ["kind: cisco_n9kv", f"image: {N9KV_IMAGE}", f"mgmt-ipv4: {mgmt[n]}", f"startup-config: configs/{n}.cfg",
                         "env:"] + [f"  {k}: {val}" for k, val in N9KV_ENV.items()])
            else:
                standby = fw_standby(role, n)
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
        # the PROD test hosts, one per access port (every access port gets its own host)
        access = sorted(((n, ifname, i["segment"]) for n in local for ifname, i in local[n]["ifs"].items()
                         if i["role"] == "access" and i["segment"]), key=lambda t: (natural(t[0]), natural(t[1])))
        for h in prod_hosts(access):
            host(f"{site}-{h['key']}", lab_ip(LAB_MGMT_HOST[h["key"]]), h["prefix"],
                 gw_host=DESIGN["segment_gateway_host"], addr_host=h["addr_host"])
            extra_links.append((full_endpoint(local[h["dev"]]["role"], h["dev"], h["ifname"]),
                                f"{site}-{h['key']}:eth1"))
        # SD-WAN branch behind its carrier
        sdwan = sorted((n for n in local if local[n]["role"] == "sdwan_fw"), key=natural)
        wan_of = {use: prov for prov, use in CARRIER_WAN.items()}
        if sdwan and wan_of["branch"] in carriers:
            bname = f"{site}-BRANCH1"
            frr(bname, lab_ip(FULL_MGMT_HOST["BRANCH1"]), "branch-lab-init", sdwan[0])
            extra_links.append((f"{carriers[wan_of['branch']]}:wan", f"{bname}:wan"))
        # VPN partner behind its carrier: the site's first partner with a translated prefix
        vpn = sorted((n for n in local if local[n]["role"] == "vpn_fw"), key=natural)
        partners = sorted(e["node"]["name"]["value"] for e in data["Partners"]["edges"]
                          if any(v(t["node"], "site", "node", "name", "value") == site for t in e["node"]["translated_prefixes"]["edges"]))
        if vpn and partners and wan_of["partner"] in carriers:
            pname = f"{site}-{partners[0]}"
            frr(pname, lab_ip(FULL_MGMT_HOST["PARTNER"]), "partner-lab-init", vpn[0])
            extra_links.append((f"{carriers[wan_of['partner']]}:wan", f"{pname}:wan"))
        # one cloud exchange per cloud router, on its cloud-exchange port
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

