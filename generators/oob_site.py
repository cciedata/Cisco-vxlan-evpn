"""Phase 4 PC6: out-of-band management per datacenter (design-decisions.md "Out-of-band management", decisions
P2-114..P2-119, decisions.xlsx Open-Phase4 rows 62-67; every other value: p4-files/oob/auto_approved.md P4A-6xx).

Generator oob_site (target group oob-sites). It builds the OOB devices of one site (FortiGate A/P pair, 2 aggregation
switches as a vPC pair, 2 network-device access switches, 1 server-BMC access switch per leaf pair / single leaf,
2 console servers), their interfaces and addresses, the OOB VLANs, and every OOB cable: the management port and the
console port of every device of the site to an OOB access port / console-server port (DcimCable).

One writer per object (P4A-601): fabric_site keeps every production device and its management interface (mgmt0,
mgmt1, MgmtEth: description "OOB management", address from <site>-MGMT0); this generator never saves them, it only
reads them and points a cable at them. It owns: the OOB devices and all their interfaces, the `console` port of every
production device (role console, a role fabric_site neither creates nor reconciles), the cables, the OOB VLANs, the OOB
vPC domain, the keepalive /31 and the OOB WAN /30. The zone gateways (.1) belong to the IP plan (objects/03-ip-plan.yml):
fetched, never saved. fabric_site's outputs are untouched (this file does not import or change it).
"""
import ipaddress
import os
import re

from infrahub_sdk.generator import InfrahubGenerator

# Decided (design, rows 62-67) ------------------------------------------------------------------------------------
DEVICE_TYPE = {"oob_fw": "FG-120G", "oob_agg": "N9K-C93180YC-FX3", "oob_access": "N9K-C9348GC-FX3",
               "console_server": "OM2248-10G-L"}                     # P2-115, P2-117, row 63 B
PLATFORM_BY_ROLE = {"oob_fw": "Fortinet FortiOS", "oob_agg": "Cisco NX-OS", "oob_access": "Cisco NX-OS",
                    "console_server": "Opengear Operations Manager"}
OS_VERSION_BY_PLATFORM = {"Cisco NX-OS": "10.6(1)F", "Fortinet FortiOS": "7.6.7"}   # same releases as fabric_site; Opengear: open (notes.md)
OOB_BASE = {"DC1": 0, "DC2": 16}                 # 10.250.<base>.0/20 per DC (decided; P4A-680)
OOB_VLANS = (                                    # VLAN id (row 65 B), zone, third-octet offset in the /20, length (decided carving)
    (100, "NETMGMT", 0, 24, "network-device management"),
    (101, "OOBINFRA", 1, 24, "OOB infrastructure"),
    (102, "BMC", 4, 22, "server BMCs"),
)
GW_HOST = 1                                      # OOB FortiGate = .1 of every zone (decided for mgmt0, P4A-620 for the others)

# Auto-approved (P4A-6xx) ----------------------------------------------------------------------------------------
STATUS = "provisioning"                          # P4A-602: same values as fabric_site
INTERFACE_STATUS = "provisioning"
ADDRESS_STATUS = "active"
LINK_STATUS = "active"
VLAN_NAME = "{site}-OOB-{zone}"                  # P4A-621
FW_SUBIF = "{zone}-{vlan}"                       # P4A-622: FortiOS <role>-<VLAN> rule (Phase 2), <= 15 characters
MGMT_IF = {"oob_fw": "mgmt", "oob_agg": "mgmt0", "oob_access": "mgmt0", "console_server": "eth2"}   # P4A-611
CONSOLE_IF = "console"                           # P4A-612: every device that is not a console server
CS_PORT = "port{n}"                              # P4A-613: console-server serial ports port1..port48
NET_PORT = "Ethernet1/{n}"                       # 9348GC-FX3: 48 x 1G RJ45 = Ethernet1/1-48 (P4A-614, ❓)
ACCESS_PORTS, CS_PORTS = 48, 48
ROLE_MGMT, ROLE_CONSOLE, ROLE_ACCESS = "management", "console", "access"
ROLE_CORE, ROLE_PEERING, ROLE_UPSTREAM = "core", "peering", "upstream"
ROLE_KEEPALIVE, ROLE_PEER_LINK = "vpc_keepalive", "vpc_peer_link"

# Port slots (P4A-630): the same slot number on the OOB access switch (Ethernet1/<slot>) and on the console server
# (port<slot>); odd unit -> switch 1 / console server 1, even unit -> 2, so both members of a pair never share one.
LEAF_PREFIX = "L"                                # server leaf L<n>: slot ceil(n / 2) = 1..10
PROD_SLOT = {"S": 11, "BL": 12, "SL": 13, "BGW": 14, "EDGE": 15, "DCI": 16, "DMZ": 17, "INETFW": 18, "INTFW": 19,
             "ISP": 20, "CLOUD": 21, "SDWANFW": 22, "VPNFW": 23}          # 24-30 free (load balancers)
OOB_SLOT = {"OOBCS": 31, "OOBFW": 32, "OOBAGG": 33, "OOBNET": 34}         # OOBBMC<k>: 34 + ceil(k / 2) = 35..42
CROSS_MGMT = {"OOBCS", "OOBNET"}                 # P4A-631: mgmt on the OTHER access switch (console server n -> switch 3-n)

# OOB devices (P4A-603): (name prefix, role, count, description)
OOB_FIXED = (
    ("OOBFW", "oob_fw", 2, "OOB firewall unit {n} (active/passive pair)"),
    ("OOBAGG", "oob_agg", 2, "OOB aggregation switch {n}"),
    ("OOBNET", "oob_access", 2, "OOB access switch {n}, network-device management ports"),
)
CS_DESCRIPTION = "Console server {n}"

# Switch-to-switch links (P4A-632..635)
AGG_DOWNLINK = "Ethernet1/{i}"                   # access switch index i (OOBNET1 = 1, OOBNET2 = 2, OOBBMC<k> = 2 + k): Po i, vPC i
AGG_FW_PORT = {1: "Ethernet1/21", 2: "Ethernet1/22"}   # OOBFW<u>: Po 20 + u, vPC 20 + u
MAX_ACCESS_SWITCHES = 20                          # downlinks Ethernet1/1-20 (1/21-22 FW, 1/48 keepalive)
AGG_KEEPALIVE = "Ethernet1/48"
AGG_PEER_LINK = ("Ethernet1/53", "Ethernet1/54")
PEER_LINK_PO = "port-channel1000"                # same number as every fabric vPC pair
OOB_VPC_DOMAIN = {"DC1": 199, "DC2": 299}        # P4A-634: top of the site range (fabric uses base + 1..15)
PAIR_NAME = "{site}-OOB-VPC-OOBAGG1-OOBAGG2"      # does not start with <site>-VPC-: fabric_site's reconcile never sees it
KEEPALIVE_THIRD = 3                              # P4A-635: <oob>.3.0/31, AGG1 .0, AGG2 .1
ACCESS_UPLINKS = ("Ethernet1/49", "Ethernet1/50")    # 25G SFP28 -> AGG1, AGG2 (P4A-632)
ACCESS_UPLINK_PO = "port-channel1"
FW_UPLINKS = ("x1", "x2")                        # 10G SFP+ -> AGG1, AGG2 (P4A-650)
FW_AGG = "agg-oob"
FW_HA = ("ha", "port16")                         # P4A-651: heartbeat, direct cables between the units
FW_WAN = "port1"                                 # P4A-652: 1G RJ45 toward the OOB carrier
OOB_WAN = {"DC1": "192.0.2.64/30", "DC2": "192.0.2.68/30"}   # P4A-653: placeholder (documentation range)
WAN_CARRIER_HOST, WAN_OUR_HOST = 1, 2            # same rule as the ISP carrier /30s
WAN_PROVIDER = "OOB-ISP"                         # P4A-653: third carrier, no provider object yet
RA_THIRD, RA_IF = 2, "RA-DIALUP"                 # P4A-660: remote-access pool <oob>.2.0/24, tunnel address .1 on unit 1

ARTIFACT_GROUPS = {"oob_agg": "oob-switches", "oob_access": "oob-switches", "oob_fw": "oob-fws"}   # P4A-604
OOB_ROLES = set(DEVICE_TYPE)

DRY_RUN = os.environ.get("FABRIC_DRY_RUN", "0") == "1"            # same switches as fabric_site (decided Phase 2)
RECONCILE = os.environ.get("FABRIC_RECONCILE", "delete")
if RECONCILE not in ("delete", "report"):
    raise ValueError(f"FABRIC_RECONCILE must be 'delete' or 'report', got {RECONCILE!r}")
PREFIX_ROLES = {ROLE_KEEPALIVE, ROLE_UPSTREAM}   # interfaces whose link prefix this generator owns


def split_name(site: str, name: str) -> tuple[str, int]:
    m = re.fullmatch(rf"{re.escape(site)}-([A-Z]+?)(\d+)", name)
    if not m:
        raise ValueError(f"{name}: no OOB slot rule for this name")
    return m.group(1), int(m.group(2))


def slot_of(site: str, name: str) -> int:
    prefix, n = split_name(site, name)
    if prefix == LEAF_PREFIX:
        return (n + 1) // 2
    if prefix == "OOBBMC":
        return OOB_SLOT["OOBNET"] + (n + 1) // 2
    slot = PROD_SLOT.get(prefix, OOB_SLOT.get(prefix))
    if slot is None:
        raise ValueError(f"{name}: prefix {prefix} has no OOB port slot (PROD_SLOT / OOB_SLOT); decide it first")
    return slot


def zone_net(site: str, vlan: int) -> ipaddress.IPv4Network:
    v = next(x for x in OOB_VLANS if x[0] == vlan)
    return ipaddress.ip_network(f"10.250.{OOB_BASE[site] + v[2]}.0/{v[3]}")


def leaf_groups(vpc: int, esi: int, single: int) -> list[str]:
    """One label per BMC access switch: the leaf pair or single leaf it serves (fabric_site plan order)."""
    out, n = [], 1
    for _ in range(vpc + esi):
        out.append(f"L{n}/L{n + 1}"); n += 2
    for _ in range(single):
        out.append(f"L{n}"); n += 1
    return out


def build_oob(site: str, counts: dict, prod: list[dict]) -> dict:
    """The whole OOB plan of a site. `prod`: [{"name", "role", "mgmt"}] = every production device with its management
    interface name (read from Infrahub). Returns devices, interfaces (device -> name -> spec), console ports on the
    production devices, cables, VLANs, the vPC pair, fixed prefixes."""
    if site not in OOB_BASE:
        raise ValueError(f"{site}: no OOB block in OOB_BASE")
    base = OOB_BASE[site]
    groups = leaf_groups(counts["vpc_leaf_pairs"], counts["esi_leaf_pairs"], counts["single_leaves"])
    devices = []
    for prefix, role, count, desc in OOB_FIXED:
        for n in range(1, count + 1):
            devices.append({"name": f"{site}-{prefix}{n}", "role": role, "description": desc.format(n=n)})
    for k, g in enumerate(groups, start=1):
        devices.append({"name": f"{site}-OOBBMC{k}", "role": "oob_access",
                        "description": f"OOB access switch, server BMCs of {site}-{g}"})
    for n in (1, 2):
        devices.append({"name": f"{site}-OOBCS{n}", "role": "console_server", "description": CS_DESCRIPTION.format(n=n)})
    for d in devices:
        d["platform"] = PLATFORM_BY_ROLE[d["role"]]
        d["mgmt"] = MGMT_IF[d["role"]]
    oob_names = [d["name"] for d in devices]
    access = [d["name"] for d in devices if d["role"] == "oob_access"]          # OOBNET1, OOBNET2, OOBBMC1..
    if len(access) > MAX_ACCESS_SWITCHES:
        raise ValueError(f"{site}: {len(access)} OOB access switches exceed the aggregation port map ({MAX_ACCESS_SWITCHES})")
    net = {1: f"{site}-OOBNET1", 2: f"{site}-OOBNET2"}
    cs = {1: f"{site}-OOBCS1", 2: f"{site}-OOBCS2"}
    agg = {1: f"{site}-OOBAGG1", 2: f"{site}-OOBAGG2"}
    fw = {1: f"{site}-OOBFW1", 2: f"{site}-OOBFW2"}
    vlans = []
    for vid, zone, third, plen, purpose in OOB_VLANS:
        vlans.append({"name": VLAN_NAME.format(site=site, zone=zone.removeprefix("OOB")), "vlan": vid, "zone": zone,
                      "prefix": str(zone_net(site, vid)), "description": f"OOB VLAN: {purpose}"})
    vlan_name = {v["vlan"]: v["name"] for v in vlans}

    ifs: dict = {n: {} for n in oob_names}
    used: list = []                                                   # (device, port) guard

    def phys(dev, name, desc, role, **kw):
        if (dev, name) in used:
            raise ValueError(f"port used twice: {dev} {name}")
        used.append((dev, name))
        ifs[dev][name] = {"kind": "InterfacePhysical", "name": name, "desc": desc, "role": role, **kw}

    def pch(dev, name, desc, role, members, **kw):
        ifs[dev][name] = {"kind": "InterfacePortChannel", "name": name, "desc": desc, "role": role, "members": list(members), **kw}

    def virt(dev, name, desc, role, **kw):
        ifs[dev][name] = {"kind": "InterfaceVirtual", "name": name, "desc": desc, "role": role, **kw}

    cables = []

    def cable(a_dev, a_port, b_dev, b_port, ctype):
        cables.append({"name": f"{a_dev} {a_port} <-> {b_dev} {b_port}", "a": (a_dev, a_port), "b": (b_dev, b_port), "type": ctype})

    # ---- management ports: every device of the site -> an OOB access port (VLAN 100 production, 101 OOB infrastructure)
    consoles = {}                                                     # production device -> console spec
    mgmt_sw, console_cs = {}, {}                                      # for the pair-split guard
    all_devs = [(p["name"], p["mgmt"], False) for p in prod] + [(d["name"], d["mgmt"], True) for d in devices]
    for name, mgmt_if, is_oob in all_devs:
        prefix, n = split_name(site, name)
        slot = slot_of(site, name)
        side = 1 if n % 2 else 2
        mside = 3 - side if prefix in CROSS_MGMT else side
        port = NET_PORT.format(n=slot)
        if slot > ACCESS_PORTS:
            raise ValueError(f"{name}: slot {slot} beyond {ACCESS_PORTS} ports")
        vid = 101 if is_oob else 100
        phys(net[mside], port, f"{name} {mgmt_if}", ROLE_ACCESS, l2_mode="access", access_vlan=vid, mgmt_of=name)
        if is_oob:
            phys(name, mgmt_if, f"{net[mside]} {port}", ROLE_MGMT, addr=("pool", f"{name}-{mgmt_if}"))
        cable(name, mgmt_if, net[mside], port, "copper")
        mgmt_sw[name] = net[mside]
        # console: every device except the console servers -> console server <side> port<slot>
        if prefix != "OOBCS":
            cport = CS_PORT.format(n=slot)
            phys(cs[side], cport, f"{name} {CONSOLE_IF}", ROLE_CONSOLE, console_of=name)
            if is_oob:
                phys(name, CONSOLE_IF, f"{cs[side]} {cport}", ROLE_CONSOLE)
            else:
                if (name, CONSOLE_IF) in used:
                    raise ValueError(f"port used twice: {name} {CONSOLE_IF}")
                used.append((name, CONSOLE_IF))
                consoles[name] = {"kind": "InterfacePhysical", "name": CONSOLE_IF, "desc": f"{cs[side]} {cport}", "role": ROLE_CONSOLE}
            cable(name, CONSOLE_IF, cs[side], cport, "console")
            console_cs[name] = cs[side]

    # ---- access switch uplinks: 1 x 25G to each aggregation switch, LACP port-channel (vPC on the aggregation pair)
    for i, sw in enumerate(access, start=1):
        tagged = [100, 101] if sw in net.values() else [102]
        members = []
        for a, up in enumerate(ACCESS_UPLINKS, start=1):
            dport = AGG_DOWNLINK.format(i=i)
            phys(sw, up, f"{agg[a]} {dport}", ROLE_CORE)
            phys(agg[a], dport, f"{sw} {up}", ROLE_CORE)
            cable(sw, up, agg[a], dport, "fiber")
            members.append(up)
        pch(sw, ACCESS_UPLINK_PO, f"{agg[1]}/OOBAGG2 port-channel{i}", ROLE_CORE, members, tagged=tagged)
        for a in (1, 2):
            pch(agg[a], f"port-channel{i}", f"{sw} {ACCESS_UPLINK_PO}", ROLE_CORE, [AGG_DOWNLINK.format(i=i)], tagged=tagged, vpc=i)

    # ---- aggregation pair: vPC peer-link and keepalive
    ka = ipaddress.ip_network(f"10.250.{base + KEEPALIVE_THIRD}.0/31")
    for a in (1, 2):
        peer = agg[3 - a]
        for p in AGG_PEER_LINK:
            phys(agg[a], p, f"{peer} {p}", ROLE_PEER_LINK)
        pch(agg[a], PEER_LINK_PO, f"{peer} {PEER_LINK_PO}", ROLE_PEER_LINK, AGG_PEER_LINK, tagged=[100, 101, 102])
        phys(agg[a], AGG_KEEPALIVE, f"{peer} {AGG_KEEPALIVE}", ROLE_KEEPALIVE, addr=("fixed", str(ka[a - 1]), 31),
             prefix=str(ka) if a == 1 else None)                      # the /31 is written once (AGG1)
    for p in AGG_PEER_LINK:
        cable(agg[1], p, agg[2], p, "dac")
    cable(agg[1], AGG_KEEPALIVE, agg[2], AGG_KEEPALIVE, "dac")

    # ---- OOB FortiGate pair: x1/x2 -> AGG1/AGG2 (aggregate agg-oob), HA, WAN, zone sub-interfaces (addresses on unit 1)
    wan = ipaddress.ip_network(OOB_WAN[site])
    ra = ipaddress.ip_network(f"10.250.{base + RA_THIRD}.0/24")
    for u in (1, 2):
        peer = fw[3 - u]
        for a, x in enumerate(FW_UPLINKS, start=1):
            phys(fw[u], x, f"{agg[a]} {AGG_FW_PORT[u]}", ROLE_PEERING)
            phys(agg[a], AGG_FW_PORT[u], f"{fw[u]} {x}", ROLE_PEERING)
            cable(fw[u], x, agg[a], AGG_FW_PORT[u], "fiber")
            pch(agg[a], f"port-channel{20 + u}", f"{fw[u]} {FW_AGG}", ROLE_PEERING, [AGG_FW_PORT[u]],
                tagged=[100, 101, 102], vpc=20 + u)
        pch(fw[u], FW_AGG, f"{agg[1]}/OOBAGG2 port-channel{20 + u}", ROLE_PEERING, FW_UPLINKS)
        for h in FW_HA:
            phys(fw[u], h, f"{peer} {h}", ROLE_CORE)
        phys(fw[u], FW_WAN, f"{WAN_PROVIDER} carrier handoff", ROLE_UPSTREAM,
             addr=("fixed", str(wan[WAN_OUR_HOST]), wan.prefixlen) if u == 1 else None, prefix=str(wan) if u == 1 else None)
        for vid, zone, third, plen, purpose in OOB_VLANS:
            gw = zone_net(site, vid)[GW_HOST]
            virt(fw[u], FW_SUBIF.format(zone=zone, vlan=vid), vlan_name[vid], ROLE_PEERING, parent=FW_AGG,
                 addr=("plan", f"{gw}/{plen}") if u == 1 else None)
        virt(fw[u], RA_IF, "Remote-access VPN (dial-up IKEv2, MFA)", ROLE_ACCESS,
             addr=("fixed", str(ra[1]), 24) if u == 1 else None)
    for h in FW_HA:
        cable(fw[1], h, fw[2], h, "copper")

    for name in oob_names:
        for spec in ifs[name].values():
            if spec["kind"] == "InterfaceVirtual" and len(spec["name"]) > 15 and name in fw.values():
                raise ValueError(f"{name}: FortiOS interface name {spec['name']} longer than 15 characters")

    # ---- design-check data guards: pair members on different OOB switches and console servers
    pairs: dict = {}
    for name, _, _ in all_devs:
        prefix, n = split_name(site, name)
        pairs.setdefault((prefix, (n + 1) // 2), []).append(name)
    for members in pairs.values():
        for where in (mgmt_sw, console_cs):
            hosts = [where[m] for m in members if m in where]
            if len(hosts) == 2 and hosts[0] == hosts[1]:
                raise ValueError(f"{members}: both on {hosts[0]}")
    names = [c["name"] for c in cables]
    if len(names) != len(set(names)):
        raise ValueError("duplicate cable name")
    return {"devices": devices, "ifs": ifs, "consoles": consoles, "cables": cables, "vlans": vlans,
            "pair": {"name": PAIR_NAME.format(site=site), "members": (agg[1], agg[2]), "domain_id": OOB_VPC_DOMAIN[site]},
            "keepalive": str(ka), "wan": {"prefix": str(wan), "ours": str(wan[WAN_OUR_HOST]), "theirs": str(wan[WAN_CARRIER_HOST])},
            "ra": str(ra)}


def print_plan(site: str, plan: dict) -> None:
    print(f"=== OOB PLAN {site}: devices {len(plan['devices'])}, cables {len(plan['cables'])}, "
          f"production console ports {len(plan['consoles'])}")
    print("--- devices: name | role | platform | device type | mgmt interface (pool)")
    for d in plan["devices"]:
        print(f"  {d['name']:<15} {d['role']:<15} {d['platform']:<28} {DEVICE_TYPE[d['role']]:<17} {d['mgmt']} ({site}-OOB-INFRA)")
    print(f"--- vPC {plan['pair']['name']} domain {plan['pair']['domain_id']} keepalive {plan['keepalive']}; WAN {plan['wan']}; RA pool {plan['ra']}")
    print("--- VLANs: " + ", ".join(f"{v['vlan']} {v['name']} {v['prefix']}" for v in plan["vlans"]))
    print("--- interfaces per OOB device")
    for dev, specs in plan["ifs"].items():
        print(f"  {dev:<15} " + ", ".join(f"{s['name']}" + (f"[v{s['access_vlan']}]" if s.get("access_vlan") else "")
                                           + (f"={s['addr'][1]}" if s.get("addr") and s["addr"][0] != "pool" else "") for s in specs.values()))
    print("--- cables: A (cabled device) <-> B (OOB side) | type")
    for c in plan["cables"]:
        print(f"  {c['name']:<62} {c['type']}")


class OobSiteGenerator(InfrahubGenerator):
    async def upsert(self, kind: str, data: dict):
        obj = await self.client.create(kind=kind, data=data)
        await obj.save(allow_upsert=True)
        return obj

    async def allocate(self, pool, identifier: str, data: dict):
        address = await self.client.allocate_next_ip_address(resource_pool=pool, identifier=identifier, data=data)
        if address is None:
            raise ValueError(f"{identifier}: pool {pool.name.value} returned nothing")
        return address

    async def fixed_prefix(self, prefix: str, description: str, site, namespace):
        """Get-or-create, saved on every run (fabric_site rule, job 032)."""
        existing = await self.client.get(kind="IpamPrefix", prefix__value=prefix, raise_when_missing=False)
        if existing is not None:
            for attr, value in (("description", description), ("status", LINK_STATUS), ("member_type", "address"), ("role", "link")):
                if getattr(existing, attr).value != value:
                    getattr(existing, attr).value = value
            if existing.site.id != site.id:
                existing.site = site
            await existing.save()
            return existing
        obj = await self.client.create(kind="IpamPrefix", data={"prefix": prefix, "description": description, "status": LINK_STATUS,
                                                                 "member_type": "address", "role": "link", "ip_namespace": namespace.id, "site": site.id})
        await obj.save()
        return obj

    async def production_devices(self, site_name: str) -> list[dict]:
        """Every device of the site that is not an OOB device, with its management interface (fabric_site writes them)."""
        r = await self.client.execute_graphql(query=(
            'query { DcimDevice(location__name__value: "%s") { edges { node { id name { value } role { value } '
            'interfaces(role__value: "management") { edges { node { id name { value } } } } } } } }' % site_name))
        out = []
        for e in r["DcimDevice"]["edges"]:
            n = e["node"]
            if n["role"]["value"] in OOB_ROLES or n["role"]["value"] == "pdu" or not n["name"]["value"].startswith(f"{site_name}-"):  # pdu: diagrams P2-239
                continue                                                  # P4A-680: only this DC's own devices
            mg = [x["node"] for x in n["interfaces"]["edges"]]
            if len(mg) != 1:
                raise ValueError(f"{n['name']['value']}: expected 1 management interface, got {len(mg)} (run fabric_site first)")
            out.append({"id": n["id"], "name": n["name"]["value"], "role": n["role"]["value"], "mgmt": mg[0]["name"]["value"], "mgmt_id": mg[0]["id"]})
        return sorted(out, key=lambda d: d["name"])

    # ---------------------------------------------------------------- reconciliation
    async def remove(self, label: str, node) -> None:
        if RECONCILE == "delete":
            await node.delete()
            print(f"DELETED       {label}")
        else:
            print(f"WOULD DELETE  {label}")

    async def remove_interface(self, dev: str, intf, keep: set, done: set) -> None:
        addrs = []
        rel = getattr(intf, "ip_addresses", None)
        for peer in (rel.peers if rel is not None else []):
            a = await self.client.get(kind="IpamIPAddress", id=peer.id, raise_when_missing=False)
            if a is not None and str(a.address.value) not in keep:
                addrs.append(a)
        await self.remove(f"Interface     {dev} {intf.name.value}", intf)
        for a in addrs:
            await self.remove(f"IpamIPAddress {a.address.value} ({dev} {intf.name.value})", a)
            if intf.role.value in PREFIX_ROLES:
                network = str(ipaddress.ip_interface(str(a.address.value)).network)
                if network not in done:
                    done.add(network)
                    p = await self.client.get(kind="IpamPrefix", prefix__value=network, raise_when_missing=False)
                    if p is not None:
                        await self.remove(f"IpamPrefix    {network}", p)

    async def reconcile(self, site_name: str, plan: dict, prod: list[dict], keep: set) -> None:
        exp_devices = {d["name"] for d in plan["devices"]}
        exp_cables = {c["name"] for c in plan["cables"]}
        exp_vlans = {v["name"] for v in plan["vlans"]}
        done: set = set()
        for pair in await self.client.filters(kind="FabricVpcDomain"):
            if pair.name.value.startswith(f"{site_name}-OOB-") and pair.name.value != plan["pair"]["name"]:
                await self.remove(f"Pair          {pair.name.value}", pair)
        for c in await self.client.filters(kind="DcimCable"):
            if c.name.value.startswith(f"{site_name}-") and " <-> " in c.name.value and c.name.value not in exp_cables:  # own cables only (diagrams P2-238)
                await self.remove(f"Cable         {c.name.value}", c)
        for v in await self.client.filters(kind="FabricL2Vlan", site__name__value=site_name):
            if v.name.value.startswith(f"{site_name}-OOB-") and v.name.value not in exp_vlans:
                await self.remove(f"L2Vlan        {v.name.value}", v)
        if prod:
            for intf in await self.client.filters(kind="InterfacePhysical", device__ids=[p["id"] for p in prod], role__value=ROLE_CONSOLE,
                                                  include=["device"]):
                dev = next((p["name"] for p in prod if p["id"] == intf.device.id), None)
                if intf.role.value == ROLE_CONSOLE and dev not in plan["consoles"]:
                    await self.remove_interface(str(dev), intf, keep, done)
        oob = [d for d in await self.client.filters(kind="DcimDevice", location__name__value=site_name) if d.role.value in OOB_ROLES]
        for d in oob:
            name = d.name.value
            expected = set(plan["ifs"].get(name, {}))
            for kind in ("InterfaceVirtual", "InterfacePortChannel", "InterfacePhysical"):
                for intf in await self.client.filters(kind=kind, device__ids=[d.id], include=["ip_addresses"] if kind != "InterfacePortChannel" else None):
                    if name not in exp_devices or intf.name.value not in expected:
                        await self.remove_interface(name, intf, keep, done)
            if name not in exp_devices:
                await self.remove(f"Device        {name}", d)
        print(f"oob reconcile ({RECONCILE}) done")

    # ---------------------------------------------------------------- generation
    async def generate(self, data: dict) -> None:
        edges = data["LocationSite"]["edges"]
        if len(edges) != 1:
            raise ValueError(f"expected exactly 1 site, got {len(edges)}")
        node = edges[0]["node"]
        site_name = node["name"]["value"]
        if site_name not in OOB_BASE:
            # P4A-680: OOB is per DC (design); a site without an OOB_BASE entry gets nothing
            print(f"{site_name}: no OOB in this site (OOB_BASE has {sorted(OOB_BASE)}); nothing written")
            return
        counts = {k: node[k].get("value") if isinstance(node.get(k), dict) else None for k in ("vpc_leaf_pairs", "esi_leaf_pairs", "single_leaves")}
        if any(v is None for v in counts.values()):
            raise ValueError(f"{site_name}: leaf counts not set: {counts}")
        prod = await self.production_devices(site_name)
        plan = build_oob(site_name, counts, prod)
        if DRY_RUN:
            print_plan(site_name, plan)
            print("DRY RUN: nothing written")
            return

        site = await self.client.get(kind="LocationSite", name__value=site_name)
        namespace = await self.client.get(kind="IpamNamespace", name__value="default")
        platforms = {p: await self.client.get(kind="DcimPlatform", name__value=p) for p in set(PLATFORM_BY_ROLE.values())}
        types = {t: await self.client.get(kind="DcimDeviceType", name__value=t) for t in set(DEVICE_TYPE.values())}
        pool = await self.client.get(kind="CoreIPAddressPool", name__value=f"{site_name}-OOB-INFRA")
        groups = {g: await self.client.get(kind="CoreStandardGroup", name__value=g) for g in set(ARTIFACT_GROUPS.values())}
        # IP-plan owned objects: fetched, never saved (they never join this generator's tracking group)
        plan_addr = {}
        for specs in plan["ifs"].values():
            for s in specs.values():
                if s.get("addr") and s["addr"][0] == "plan":
                    plan_addr[s["addr"][1]] = await self.client.get(kind="IpamIPAddress", address__value=s["addr"][1])
        vlan_prefix = {v["name"]: await self.client.get(kind="IpamPrefix", prefix__value=v["prefix"]) for v in plan["vlans"]}

        # ---- devices + management interface (address from <site>-OOB-INFRA)
        devices = {}
        mgmt_addr = {}
        for d in plan["devices"]:
            ident = f"{d['name']}-{d['mgmt']}"
            addr = await self.allocate(pool, ident, {"description": f"{d['name']} {d['mgmt']}", "status": ADDRESS_STATUS})
            mgmt_addr[d["name"]] = addr
            payload = {"name": d["name"], "description": d["description"], "status": STATUS, "role": d["role"], "location": site.id,
                       "platform": platforms[d["platform"]].id, "device_type": types[DEVICE_TYPE[d["role"]]].id, "primary_address": addr.id}
            if OS_VERSION_BY_PLATFORM.get(d["platform"]):
                payload["os_version"] = OS_VERSION_BY_PLATFORM[d["platform"]]
            devices[d["name"]] = await self.upsert("DcimDevice", payload)
            print(f"{d['name']:<15} {d['role']:<15} {d['mgmt']}={addr.address.value}")

        # ---- vPC domain of the aggregation pair, OOB VLANs on it
        m1, m2 = plan["pair"]["members"]
        pair = await self.upsert("FabricVpcDomain", {"name": plan["pair"]["name"], "domain_id": plan["pair"]["domain_id"],
                                                     "members": [devices[m1].id, devices[m2].id]})
        vlans = {}
        for v in plan["vlans"]:
            vlans[v["vlan"]] = await self.upsert("FabricL2Vlan", {"name": v["name"], "vlan_id": v["vlan"], "description": v["description"],
                                                                  "switch_pair": pair.id, "site": site.id, "prefix": vlan_prefix[v["name"]].id})

        # ---- interfaces on the OOB devices: physical, then port-channels (members), then virtual (parent)
        objs: dict = {n: {} for n in plan["ifs"]}
        for kind in ("InterfacePhysical", "InterfacePortChannel", "InterfaceVirtual"):
            for dev, specs in plan["ifs"].items():
                for s in specs.values():
                    if s["kind"] != kind:
                        continue
                    data_ = {"name": s["name"], "device": devices[dev].id, "description": s["desc"], "role": s["role"], "status": INTERFACE_STATUS}
                    addr = s.get("addr")
                    if addr:
                        if addr[0] == "pool":
                            a = mgmt_addr[dev]
                        elif addr[0] == "plan":
                            a = plan_addr[addr[1]]
                        else:
                            if s.get("prefix"):
                                desc = (f"{plan['pair']['name']} vPC peer-keepalive" if s["role"] == ROLE_KEEPALIVE
                                        else f"{dev[:-1]} <-> {WAN_PROVIDER} handoff (placeholder)")
                                await self.fixed_prefix(s["prefix"], desc, site, namespace)
                                if s["role"] == ROLE_UPSTREAM:
                                    await self.upsert("IpamIPAddress", {"address": f"{plan['wan']['theirs']}/{addr[2]}", "status": LINK_STATUS,
                                                                        "description": f"{WAN_PROVIDER} side of the {dev[:-1]} handoff (placeholder)",
                                                                        "ip_namespace": namespace.id})
                            shared = " (shared by both units)" if dev.startswith(f"{site_name}-OOBFW") else ""
                            a = await self.upsert("IpamIPAddress", {"address": f"{addr[1]}/{addr[2]}", "status": LINK_STATUS,
                                                                    "description": f"{dev[:-1] if shared else dev} {s['name']}{shared}", "ip_namespace": namespace.id})
                        data_["ip_addresses"] = [a.id]
                    if s.get("l2_mode"):
                        data_["l2_mode"] = s["l2_mode"]
                    if s.get("access_vlan"):
                        data_["access_vlan"] = vlans[s["access_vlan"]].id
                    if kind == "InterfacePortChannel":
                        data_["members"] = [objs[dev][m].id for m in s["members"]]
                        if s.get("tagged"):
                            data_["l2_mode"] = "trunk"
                            data_["tagged_vlans"] = [vlans[t].id for t in s["tagged"]]
                    if s.get("parent"):
                        data_["parent_interface"] = objs[dev][s["parent"]].id
                    objs[dev][s["name"]] = await self.upsert(kind, data_)

        # ---- console port of every production device (role console; fabric_site never touches it)
        prod_by_name = {p["name"]: p for p in prod}
        console_objs = {}
        for name, s in plan["consoles"].items():
            console_objs[name] = await self.upsert("InterfacePhysical", {"name": s["name"], "device": prod_by_name[name]["id"], "description": s["desc"],
                                                                         "role": s["role"], "status": INTERFACE_STATUS})

        # ---- cables (endpoint A = the cabled device, B = the OOB side)
        def endpoint(dev: str, port: str):
            if dev in objs:
                return objs[dev][port].id
            if port == CONSOLE_IF:
                return console_objs[dev].id
            if prod_by_name[dev]["mgmt"] == port:
                return prod_by_name[dev]["mgmt_id"]                      # fabric_site's interface: referenced, never saved
            raise ValueError(f"cable endpoint {dev} {port} unknown")
        for c in plan["cables"]:
            await self.upsert("DcimCable", {"name": c["name"], "cable_type": c["type"],
                                            "connected_endpoints": [endpoint(*c["a"]), endpoint(*c["b"])]})
        print(f"{site_name}: {len(plan['devices'])} OOB devices, {len(plan['consoles'])} production console ports, {len(plan['cables'])} cables")

        # ---- artifact target groups (add only)
        for g in groups.values():
            await g.members.fetch()
            have = {p.id for p in g.members.peers}
            new = [devices[d["name"]].id for d in plan["devices"] if ARTIFACT_GROUPS.get(d["role"]) == g.name.value and devices[d["name"]].id not in have]
            for i in new:
                g.members.add(i)
            if new:
                await g.save()
            print(f"group {g.name.value:<14} +{len(new)}")

        await self.reconcile(site_name, plan, prod, keep=set(plan_addr))
