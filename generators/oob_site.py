"""Out-of-band management per datacenter.

Generator oob_site (target group oob-sites). It builds the OOB devices of one site (FortiGate A/P pair, oob_agg_count
aggregation switches as a vPC pair, oob_access_count network-device access switches, 1 server-BMC access switch per leaf
pair / single leaf, console_server_count console servers), their interfaces and addresses, the OOB VLANs, and every OOB cable: the management port and the
console port of every device of the site to an OOB access port / console-server port (DcimCable).

One writer per object: fabric_site keeps every production device and its management interface (mgmt0,
mgmt1, MgmtEth: description "OOB management", address from <site>-MGMT0); this generator never saves them, it only
reads them and points a cable at them. It owns: the OOB devices and all their interfaces, the `console` port of every
production device (role console, a role fabric_site neither creates nor reconciles), the cables, the OOB VLANs, the OOB
vPC domain, the keepalive /31 and the OOB WAN /30. The zone gateways (.1) belong to the IP plan (objects/03-ip-plan.yml):
fetched, never saved. fabric_site's outputs are untouched (this file does not import or change it).
"""
import contextvars
import ipaddress
import os
import re

from infrahub_sdk.generator import InfrahubGenerator

# Design values live in Infrahub: FabricDesign (global, per-site dc1_/dc2_, per-role fields),
# DcimDeviceType (port defaults of the OOB models) and DcimPlatform (os_version). load_values() / load_type_values()
# fill D at the start of generate(); a missing or empty value stops the generator with the field name.
class _RunValues:
    """Per-run design values. Infrahub runs the generator for DC1 and DC2 concurrently in one worker process
    (asyncio tasks), so a plain module-level dict would be shared between the two sites (PC validator, 24 Oct).
    Each asyncio task gets its own dict through a ContextVar; code keeps using D[...] as before."""
    _var = contextvars.ContextVar("design_run_values")

    def _d(self) -> dict:
        try:
            return self._var.get()
        except LookupError:
            d = {}
            self._var.set(d)
            return d

    def reset(self) -> None:
        self._var.set({})

    def __getitem__(self, k):
        return self._d()[k]

    def __setitem__(self, k, v):
        self._d()[k] = v

    def __contains__(self, k):
        return k in self._d()

    def get(self, k, default=None):
        return self._d().get(k, default)

    def clear(self) -> None:
        self.reset()


D = _RunValues()
OOB_ROLE_LIST = ("oob_fw", "oob_agg", "oob_access", "console_server")
ARTIFACT_ROLES = ("oob_agg", "oob_access", "oob_fw")                       # roles with an artifact group
OOB_VLAN_PURPOSE = {"NETMGMT": "network-device management", "OOBINFRA": "OOB infrastructure", "BMC": "server BMCs"}

VLAN_NAME = "{site}-OOB-{zone}"
FW_SUBIF = "{zone}-{vlan}"                       # FortiOS <role>-<VLAN> rule, <= 15 characters
ROLE_MGMT, ROLE_CONSOLE, ROLE_ACCESS = "management", "console", "access"
ROLE_CORE, ROLE_PEERING, ROLE_UPSTREAM = "core", "peering", "upstream"
ROLE_KEEPALIVE, ROLE_PEER_LINK = "vpc_keepalive", "vpc_peer_link"

# Port slots (FabricDesign.oob_port_slots): the same slot number on the OOB access switch and on the console
# server. Unit k of a slot goes to switch k / console server k (FabricDesign.oob_units_per_port_slot units per slot), so
# both members of a pair never share one.
LEAF_PREFIX = "L"                                # server leaf L<n>: slot ceil(n / oob_units_per_port_slot)

# OOB devices: (name prefix, role, description); the count is FabricDesign.<role>_count
OOB_FIXED = (
    ("OOBFW", "oob_fw", "OOB firewall unit {n} (active/passive pair)"),
    ("OOBAGG", "oob_agg", "OOB aggregation switch {n}"),
    ("OOBNET", "oob_access", "OOB access switch {n}, network-device management ports"),
)
CS_DESCRIPTION = "Console server {n}"

PAIR_NAME = "{site}-OOB-VPC-OOBAGG1-OOBAGG2"      # does not start with <site>-VPC-: fabric_site's reconcile never sees it
FW_AGG = "agg-oob"
# OOB WAN carrier (third carrier, no provider object yet): FabricDesign.oob_carrier_name (D["wan_provider"])
RA_IF = "RA-DIALUP"                              # remote-access interface on the OOB FortiGate

OOB_ROLES = set(OOB_ROLE_LIST)

DRY_RUN = os.environ.get("FABRIC_DRY_RUN", "0") == "1"            # same switches as fabric_site
RECONCILE = os.environ.get("FABRIC_RECONCILE", "delete")
if RECONCILE not in ("delete", "report"):
    raise ValueError(f"FABRIC_RECONCILE must be 'delete' or 'report', got {RECONCILE!r}")
PREFIX_ROLES = {ROLE_KEEPALIVE, ROLE_UPSTREAM}   # interfaces whose link prefix this generator owns


async def load_design(client):
    nodes = await client.all(kind="FabricDesign")
    if len(nodes) != 1:
        raise ValueError(f"FabricDesign: expected exactly 1 object, found {len(nodes)}; fix it in Infrahub")
    return nodes[0]


def need(design, field):
    try:
        value = getattr(design, field).value
    except AttributeError:
        value = None                                              # no such field (e.g. a site not in the schema yet)
    if value is None:
        raise ValueError(f"FabricDesign.{field} is empty; fix it in Infrahub")
    return value


def need_pair(design, role: str) -> int:
    count = need(design, f"{role}_count")
    if count != 2:
        raise ValueError(f"{role}_count must be 2 (pair logic)")
    return count


def need_type(dtype, field):
    try:
        value = getattr(dtype, field).value
    except AttributeError:
        value = None
    if value is None:
        raise ValueError(f"DcimDeviceType {dtype.name.value}.{field} is empty; fix it in Infrahub")
    return value


def load_values(design, site: str) -> None:
    """FabricDesign values of this generator for one site (fills D)."""
    D.clear()
    s = site.lower()
    D["device_type"] = {r: need(design, f"{r}_device_type") for r in OOB_ROLE_LIST}
    D["platform"] = {r: need(design, f"{r}_platform") for r in OOB_ROLE_LIST}
    D["mgmt"] = {r: need(design, f"{r}_mgmt_interface") for r in OOB_ROLE_LIST}
    D["count"] = {r: need_pair(design, r) for r in OOB_ROLE_LIST}
    D["artifact_groups"] = {r: need(design, f"{r}_artifact_group") for r in ARTIFACT_ROLES}
    D["oob_base"] = need(design, f"{s}_oob_block_base")
    D["oob_wan"] = str(need(design, f"{s}_oob_wan_prefix"))
    D["vpc_domain"] = need(design, f"{s}_oob_vpc_domain")
    D["supernet"] = ipaddress.ip_network(str(need(design, "oob_supernet")))
    vlans = []
    for v in need(design, "oob_vlans"):
        if v["zone"] not in OOB_VLAN_PURPOSE:
            raise ValueError(f"FabricDesign.oob_vlans: zone {v['zone']} has no VLAN description in OOB_VLAN_PURPOSE")
        vlans.append((v["vlan"], v["zone"], v["third_offset"], v["prefix_length"], OOB_VLAN_PURPOSE[v["zone"]]))
    D["vlans"] = tuple(vlans)
    D["vlan_by_zone"] = {v[1]: v[0] for v in vlans}
    if set(D["vlan_by_zone"]) != set(OOB_VLAN_PURPOSE):
        raise ValueError(f"FabricDesign.oob_vlans: zones {sorted(D['vlan_by_zone'])}, expected {sorted(OOB_VLAN_PURPOSE)}")
    D["gw_host"] = need(design, "oob_gateway_host")
    D["status_device"] = need(design, "status_device")
    D["status_interface"] = need(design, "status_interface")
    D["status_address"] = need(design, "status_address")
    D["status_link"] = need(design, "status_link")
    D["slots"] = dict(need(design, "oob_port_slots"))
    D["cross_mgmt"] = set(need(design, "oob_cross_mgmt_prefixes"))
    D["agg_fw_port"] = {int(k): v for k, v in need(design, "oob_agg_fw_ports").items()}
    D["max_access"] = need(design, "oob_max_access_switches")
    D["peer_link_po"] = f"port-channel{need(design, 'vpc_peer_link_port_channel')}"
    D["access_uplink_po"] = f"port-channel{need(design, 'oob_access_uplink_port_channel')}"
    D["fw_po_base"] = need(design, "oob_fw_port_channel_base")
    D["keepalive_third"] = need(design, "oob_keepalive_third_octet")
    D["p2p_len"] = need(design, "p2p_link_prefix_length")
    D["wan_carrier_host"] = need(design, "oob_wan_carrier_host")
    D["wan_our_host"] = need(design, "oob_wan_our_host")
    D["ra_third"] = need(design, "oob_ra_pool_third_octet")
    D["ra_len"] = need(design, "oob_ra_pool_prefix_length")
    D["ra_host"] = need(design, "oob_ra_tunnel_host")
    D["cable_mgmt"] = need(design, "oob_cable_type_mgmt")
    D["cable_console"] = need(design, "oob_cable_type_console")
    D["cable_uplink"] = need(design, "oob_cable_type_uplink")
    D["cable_peer"] = need(design, "oob_cable_type_peer")
    D["cable_ha"] = need(design, "oob_cable_type_ha")
    D["wan_provider"] = need(design, "oob_carrier_name")
    D["slot_units"] = need(design, "oob_units_per_port_slot")
    if not 1 <= D["slot_units"] <= min(D["count"]["oob_access"], D["count"]["console_server"]):
        raise ValueError(f"FabricDesign.oob_units_per_port_slot = {D['slot_units']}: needs one OOB access switch and one console "
                         "server per unit of a slot; fix it in Infrahub")
    D["mgmt_zone"] = dict(need(design, "oob_mgmt_zone_by_class"))           # "oob" / "production" -> oob_vlans zone
    for k in ("oob", "production"):
        if D["mgmt_zone"].get(k) not in D["vlan_by_zone"]:
            raise ValueError(f"FabricDesign.oob_mgmt_zone_by_class['{k}'] = {D['mgmt_zone'].get(k)!r} is not an oob_vlans zone; fix it in Infrahub")
    D["uplink_zones"] = {k: list(v) for k, v in need(design, "oob_access_uplink_zones").items()}   # access switch prefix -> zones
    for k, zones in D["uplink_zones"].items():
        bad = [z for z in zones if z not in D["vlan_by_zone"]]
        if bad:
            raise ValueError(f"FabricDesign.oob_access_uplink_zones['{k}']: {bad} are not oob_vlans zones; fix it in Infrahub")
    D["agg_po_base"] = need(design, "oob_agg_downlink_port_channel_base")
    D["ka_host"] = {int(k): v for k, v in need(design, "oob_keepalive_member_hosts").items()}
    missing = [n for n in range(1, D["count"]["oob_agg"] + 1) if n not in D["ka_host"]]
    if missing:
        raise ValueError(f"FabricDesign.oob_keepalive_member_hosts has no entry for unit(s) {missing}; fix it in Infrahub")
    D["no_os_platforms"] = set(need(design, "oob_platforms_without_os_version"))


def load_type_values(types: dict) -> None:
    """Port defaults of the OOB models (DcimDeviceType, keyed by type name) (fills D)."""
    t = {r: types[D["device_type"][r]] for r in OOB_ROLE_LIST}
    D["console_port"] = {r: need_type(t[r], "console_port") for r in OOB_ROLE_LIST if r != "console_server"}
    D["cs_port"] = need_type(t["console_server"], "port_name_format")       # console-server serial ports
    D["net_port"] = need_type(t["oob_access"], "port_name_format")          # access-switch front-panel ports
    D["access_ports"] = need_type(t["oob_access"], "access_port_count")
    D["agg_downlink"] = need_type(t["oob_agg"], "port_name_format")         # access switch index i: Po i, vPC i
    D["agg_keepalive"] = need_type(t["oob_agg"], "vpc_keepalive_port")
    D["agg_peer_link"] = tuple(need_type(t["oob_agg"], "vpc_peer_link_ports"))
    D["access_uplinks"] = tuple(need_type(t["oob_access"], "uplink_ports"))  # -> AGG1, AGG2
    D["fw_uplinks"] = tuple(need_type(t["oob_fw"], "uplink_ports"))          # -> AGG1, AGG2
    D["fw_ha"] = tuple(need_type(t["oob_fw"], "ha_ports"))
    D["fw_wan"] = need_type(t["oob_fw"], "wan_port")
    for key in ("access_uplinks", "fw_uplinks"):
        if len(D[key]) != D["count"]["oob_agg"]:
            raise ValueError(f"{key}: {len(D[key])} uplink ports for {D['count']['oob_agg']} aggregation switches")


def split_name(site: str, name: str) -> tuple[str, int]:
    m = re.fullmatch(rf"{re.escape(site)}-([A-Z]+?)(\d+)", name)
    if not m:
        raise ValueError(f"{name}: no OOB slot rule for this name")
    return m.group(1), int(m.group(2))


def slot_of(site: str, name: str) -> int:
    prefix, n = split_name(site, name)
    units = D["slot_units"]
    if prefix == LEAF_PREFIX:
        return (n + units - 1) // units                               # ceil(n / units)
    if prefix == "OOBBMC":
        return D["slots"]["OOBNET"] + (n + units - 1) // units
    slot = D["slots"].get(prefix)
    if slot is None:
        raise ValueError(f"{name}: prefix {prefix} has no OOB port slot (FabricDesign.oob_port_slots); decide it first")
    return slot


def third_octet_net(third: int, plen: int) -> ipaddress.IPv4Network:
    """<oob_supernet>.<third>.0/<plen> (the OOB block is carved by third octet)."""
    return ipaddress.ip_network(f"{D['supernet'].network_address + (third << 8)}/{plen}")


def zone_net(site: str, vlan: int) -> ipaddress.IPv4Network:
    v = next(x for x in D["vlans"] if x[0] == vlan)
    return third_octet_net(D["oob_base"] + v[2], v[3])


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
    base = D["oob_base"]
    cnt = D["count"]
    groups = leaf_groups(counts["vpc_leaf_pairs"], counts["esi_leaf_pairs"], counts["single_leaves"])
    devices = []
    for prefix, role, desc in OOB_FIXED:
        for n in range(1, cnt[role] + 1):
            devices.append({"name": f"{site}-{prefix}{n}", "role": role, "description": desc.format(n=n)})
    for k, g in enumerate(groups, start=1):
        devices.append({"name": f"{site}-OOBBMC{k}", "role": "oob_access",
                        "description": f"OOB access switch, server BMCs of {site}-{g}"})
    for n in range(1, cnt["console_server"] + 1):
        devices.append({"name": f"{site}-OOBCS{n}", "role": "console_server", "description": CS_DESCRIPTION.format(n=n)})
    for d in devices:
        d["platform"] = D["platform"][d["role"]]
        d["mgmt"] = D["mgmt"][d["role"]]
        d["console"] = D["console_port"].get(d["role"])                  # None for a console server (no console port)
    oob_names = [d["name"] for d in devices]
    access = [d["name"] for d in devices if d["role"] == "oob_access"]          # OOBNET1, OOBNET2, OOBBMC1..
    if len(access) > D["max_access"]:
        raise ValueError(f"{site}: {len(access)} OOB access switches exceed the aggregation port map ({D['max_access']})")
    net = {n: f"{site}-OOBNET{n}" for n in range(1, cnt["oob_access"] + 1)}
    cs = {n: f"{site}-OOBCS{n}" for n in range(1, cnt["console_server"] + 1)}
    agg = {n: f"{site}-OOBAGG{n}" for n in range(1, cnt["oob_agg"] + 1)}
    fw = {n: f"{site}-OOBFW{n}" for n in range(1, cnt["oob_fw"] + 1)}
    vlans = []
    for vid, zone, third, plen, purpose in D["vlans"]:
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

    # ---- management ports: every device of the site -> an OOB access port (VLANs from oob_vlans: production, OOB infrastructure)
    consoles = {}                                                     # production device -> console spec
    mgmt_sw, console_cs = {}, {}                                      # for the pair-split guard
    all_devs = [(p["name"], p["mgmt"], p["console"], False) for p in prod] + [(d["name"], d["mgmt"], d["console"], True) for d in devices]
    zv = D["vlan_by_zone"]
    all_vids = [v[0] for v in D["vlans"]]
    for name, mgmt_if, console_if, is_oob in all_devs:
        prefix, n = split_name(site, name)
        slot = slot_of(site, name)
        side = (n - 1) % D["slot_units"] + 1                          # unit k of its slot -> switch / console server k
        mside = 3 - side if prefix in D["cross_mgmt"] else side
        port = D["net_port"].format(n=slot)
        if slot > D["access_ports"]:
            raise ValueError(f"{name}: slot {slot} beyond {D['access_ports']} ports")
        vid = zv[D["mgmt_zone"]["oob" if is_oob else "production"]]
        phys(net[mside], port, f"{name} {mgmt_if}", ROLE_ACCESS, l2_mode="access", access_vlan=vid, mgmt_of=name)
        if is_oob:
            phys(name, mgmt_if, f"{net[mside]} {port}", ROLE_MGMT, addr=("pool", f"{name}-{mgmt_if}"))
        cable(name, mgmt_if, net[mside], port, D["cable_mgmt"])
        mgmt_sw[name] = net[mside]
        # console: every device except the console servers -> console server <side> port<slot>
        if prefix != "OOBCS":
            if console_if is None:
                raise ValueError(f"{name}: device type has no console_port; fix DcimDeviceType in Infrahub")
            cport = D["cs_port"].format(n=slot)
            phys(cs[side], cport, f"{name} {console_if}", ROLE_CONSOLE, console_of=name)
            if is_oob:
                phys(name, console_if, f"{cs[side]} {cport}", ROLE_CONSOLE)
            else:
                if (name, console_if) in used:
                    raise ValueError(f"port used twice: {name} {console_if}")
                used.append((name, console_if))
                consoles[name] = {"kind": "InterfacePhysical", "name": console_if, "desc": f"{cs[side]} {cport}", "role": ROLE_CONSOLE}
            cable(name, console_if, cs[side], cport, D["cable_console"])
            console_cs[name] = cs[side]

    # ---- access switch uplinks: one port (DcimDeviceType uplink_ports) to each aggregation switch, LACP port-channel (vPC on the aggregation pair)
    access_po = D["access_uplink_po"]
    for i, sw in enumerate(access, start=1):
        kind = split_name(site, sw)[0]                                # OOBNET / OOBBMC
        if kind not in D["uplink_zones"]:
            raise ValueError(f"{sw}: FabricDesign.oob_access_uplink_zones has no entry for {kind}; fix it in Infrahub")
        tagged = [zv[z] for z in D["uplink_zones"][kind]]
        po_n = D["agg_po_base"] + i                                   # aggregation downlink port-channel / vPC number
        members = []
        for a, up in enumerate(D["access_uplinks"], start=1):
            dport = D["agg_downlink"].format(n=i)
            phys(sw, up, f"{agg[a]} {dport}", ROLE_CORE)
            phys(agg[a], dport, f"{sw} {up}", ROLE_CORE)
            cable(sw, up, agg[a], dport, D["cable_uplink"])
            members.append(up)
        pch(sw, access_po, f"{agg[1]}/OOBAGG2 port-channel{po_n}", ROLE_CORE, members, tagged=tagged)
        for a in agg:
            pch(agg[a], f"port-channel{po_n}", f"{sw} {access_po}", ROLE_CORE, [D["agg_downlink"].format(n=i)], tagged=tagged, vpc=po_n)

    # ---- aggregation pair: vPC peer-link and keepalive
    ka = third_octet_net(base + D["keepalive_third"], D["p2p_len"])
    peer_link_po, keepalive_if, peer_links = D["peer_link_po"], D["agg_keepalive"], D["agg_peer_link"]
    for a in agg:
        peer = agg[3 - a]
        for p in peer_links:
            phys(agg[a], p, f"{peer} {p}", ROLE_PEER_LINK)
        pch(agg[a], peer_link_po, f"{peer} {peer_link_po}", ROLE_PEER_LINK, peer_links, tagged=list(all_vids))
        phys(agg[a], keepalive_if, f"{peer} {keepalive_if}", ROLE_KEEPALIVE, addr=("fixed", str(ka[D["ka_host"][a]]), ka.prefixlen),
             prefix=str(ka) if a == 1 else None)                      # the /31 is written once (AGG1)
    for p in peer_links:
        cable(agg[1], p, agg[2], p, D["cable_peer"])
    cable(agg[1], keepalive_if, agg[2], keepalive_if, D["cable_peer"])

    # ---- OOB FortiGate pair: uplink_ports (DcimDeviceType) -> AGG1/AGG2 (aggregate agg-oob), HA, WAN, zone sub-interfaces (addresses on unit 1)
    wan = ipaddress.ip_network(D["oob_wan"])
    ra = third_octet_net(base + D["ra_third"], D["ra_len"])
    fw_uplinks, fw_ha, fw_wan, po_base = D["fw_uplinks"], D["fw_ha"], D["fw_wan"], D["fw_po_base"]
    for u in fw:
        peer = fw[3 - u]
        agg_fw_port = D["agg_fw_port"][u]
        for a, x in enumerate(fw_uplinks, start=1):
            phys(fw[u], x, f"{agg[a]} {agg_fw_port}", ROLE_PEERING)
            phys(agg[a], agg_fw_port, f"{fw[u]} {x}", ROLE_PEERING)
            cable(fw[u], x, agg[a], agg_fw_port, D["cable_uplink"])   # same field as the access uplink
            pch(agg[a], f"port-channel{po_base + u}", f"{fw[u]} {FW_AGG}", ROLE_PEERING, [agg_fw_port],
                tagged=list(all_vids), vpc=po_base + u)
        pch(fw[u], FW_AGG, f"{agg[1]}/OOBAGG2 port-channel{po_base + u}", ROLE_PEERING, fw_uplinks)
        for h in fw_ha:
            phys(fw[u], h, f"{peer} {h}", ROLE_CORE)
        phys(fw[u], fw_wan, f"{D['wan_provider']} carrier handoff", ROLE_UPSTREAM,
             addr=("fixed", str(wan[D["wan_our_host"]]), wan.prefixlen) if u == 1 else None, prefix=str(wan) if u == 1 else None)
        for vid, zone, third, plen, purpose in D["vlans"]:
            gw = zone_net(site, vid)[D["gw_host"]]
            virt(fw[u], FW_SUBIF.format(zone=zone, vlan=vid), vlan_name[vid], ROLE_PEERING, parent=FW_AGG,
                 addr=("plan", f"{gw}/{plen}") if u == 1 else None)
        virt(fw[u], RA_IF, "Remote-access VPN (dial-up IKEv2, MFA)", ROLE_ACCESS,
             addr=("fixed", str(ra[D["ra_host"]]), ra.prefixlen) if u == 1 else None)
    for h in fw_ha:
        cable(fw[1], h, fw[2], h, D["cable_ha"])

    for name in oob_names:
        for spec in ifs[name].values():
            if spec["kind"] == "InterfaceVirtual" and len(spec["name"]) > 15 and name in fw.values():
                raise ValueError(f"{name}: FortiOS interface name {spec['name']} longer than 15 characters")

    # ---- design-check data guards: pair members on different OOB switches and console servers
    pairs: dict = {}
    for name, _, _, _ in all_devs:
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
            "pair": {"name": PAIR_NAME.format(site=site), "members": (agg[1], agg[2]), "domain_id": D["vpc_domain"]},
            "keepalive": str(ka), "wan": {"prefix": str(wan), "ours": str(wan[D["wan_our_host"]]), "theirs": str(wan[D["wan_carrier_host"]])},
            "ra": str(ra)}


def print_plan(site: str, plan: dict) -> None:
    print(f"=== OOB PLAN {site}: devices {len(plan['devices'])}, cables {len(plan['cables'])}, "
          f"production console ports {len(plan['consoles'])}")
    print("--- devices: name | role | platform | device type | mgmt interface (pool)")
    for d in plan["devices"]:
        print(f"  {d['name']:<15} {d['role']:<15} {d['platform']:<28} {D['device_type'][d['role']]:<17} {d['mgmt']} ({site}-OOB-INFRA)")
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
        """Get-or-create, saved on every run (fabric_site rule: an unsaved prefix is deleted as unused)."""
        existing = await self.client.get(kind="IpamPrefix", prefix__value=prefix, raise_when_missing=False)
        if existing is not None:
            for attr, value in (("description", description), ("status", D["status_link"]), ("member_type", "address"), ("role", "link")):
                if getattr(existing, attr).value != value:
                    getattr(existing, attr).value = value
            if existing.site.id != site.id:
                existing.site = site
            await existing.save()
            return existing
        obj = await self.client.create(kind="IpamPrefix", data={"prefix": prefix, "description": description, "status": D["status_link"],
                                                                 "member_type": "address", "role": "link", "ip_namespace": namespace.id, "site": site.id})
        await obj.save()
        return obj

    async def production_devices(self, site_name: str) -> list[dict]:
        """Every device of the site that is not an OOB device, with its management interface (fabric_site writes them)."""
        r = await self.client.execute_graphql(query=(
            'query { DcimDevice(location__name__value: "%s") { edges { node { id name { value } role { value } '
            'device_type { node { name { value } console_port { value } } } '
            'interfaces(role__value: "management") { edges { node { id name { value } } } } } } } }' % site_name))
        out = []
        for e in r["DcimDevice"]["edges"]:
            n = e["node"]
            if n["role"]["value"] in OOB_ROLES or n["role"]["value"] == "pdu" or not n["name"]["value"].startswith(f"{site_name}-"):  # pdu: placed by site_physical
                continue                                                  # only this DC's own devices
            mg = [x["node"] for x in n["interfaces"]["edges"]]
            if len(mg) != 1:
                raise ValueError(f"{n['name']['value']}: expected 1 management interface, got {len(mg)} (run fabric_site first)")
            dt = (n.get("device_type") or {}).get("node")
            if dt is None or dt["console_port"]["value"] is None:   # console port name = DcimDeviceType.console_port
                raise ValueError(f"{n['name']['value']}: DcimDeviceType "
                                 f"{dt['name']['value'] if dt else '(none)'}.console_port is empty; fix it in Infrahub")
            out.append({"id": n["id"], "name": n["name"]["value"], "role": n["role"]["value"], "mgmt": mg[0]["name"]["value"], "mgmt_id": mg[0]["id"],
                        "console": dt["console_port"]["value"]})
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
            if c.name.value.startswith(f"{site_name}-") and " <-> " in c.name.value and c.name.value not in exp_cables:  # own cables only
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
        # OOB is per DC; the per-site FabricDesign fields (dc1_/dc2_) must exist and be set for this site
        load_values(await load_design(self.client), site_name)
        types = {t: await self.client.get(kind="DcimDeviceType", name__value=t) for t in set(D["device_type"].values())}
        load_type_values(types)
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
        platforms = {p: await self.client.get(kind="DcimPlatform", name__value=p) for p in set(D["platform"].values())}
        os_version = {p: obj.os_version.value for p, obj in platforms.items()}     # DcimPlatform.os_version
        for p, ver in sorted(os_version.items()):    # empty only where FabricDesign.oob_platforms_without_os_version allows it
            if not ver and p not in D["no_os_platforms"]:
                raise ValueError(f"DcimPlatform {p}.os_version is empty and {p} is not in FabricDesign.oob_platforms_without_os_version; "
                                 "fix it in Infrahub")
        pool = await self.client.get(kind="CoreIPAddressPool", name__value=f"{site_name}-OOB-INFRA")
        groups = {g: await self.client.get(kind="CoreStandardGroup", name__value=g) for g in set(D["artifact_groups"].values())}
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
            addr = await self.allocate(pool, ident, {"description": f"{d['name']} {d['mgmt']}", "status": D["status_address"]})
            mgmt_addr[d["name"]] = addr
            payload = {"name": d["name"], "description": d["description"], "status": D["status_device"], "role": d["role"], "location": site.id,
                       "platform": platforms[d["platform"]].id, "device_type": types[D["device_type"][d["role"]]].id, "primary_address": addr.id}
            if os_version.get(d["platform"]):
                payload["os_version"] = os_version[d["platform"]]
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
                    data_ = {"name": s["name"], "device": devices[dev].id, "description": s["desc"], "role": s["role"], "status": D["status_interface"]}
                    addr = s.get("addr")
                    if addr:
                        if addr[0] == "pool":
                            a = mgmt_addr[dev]
                        elif addr[0] == "plan":
                            a = plan_addr[addr[1]]
                        else:
                            if s.get("prefix"):
                                desc = (f"{plan['pair']['name']} vPC peer-keepalive" if s["role"] == ROLE_KEEPALIVE
                                        else f"{dev[:-1]} <-> {D['wan_provider']} handoff (placeholder)")
                                await self.fixed_prefix(s["prefix"], desc, site, namespace)
                                if s["role"] == ROLE_UPSTREAM:
                                    await self.upsert("IpamIPAddress", {"address": f"{plan['wan']['theirs']}/{addr[2]}", "status": D["status_link"],
                                                                        "description": f"{D['wan_provider']} side of the {dev[:-1]} handoff (placeholder)",
                                                                        "ip_namespace": namespace.id})
                            shared = " (shared by both units)" if dev.startswith(f"{site_name}-OOBFW") else ""
                            a = await self.upsert("IpamIPAddress", {"address": f"{addr[1]}/{addr[2]}", "status": D["status_link"],
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
                                                                         "role": s["role"], "status": D["status_interface"]})

        # ---- cables (endpoint A = the cabled device, B = the OOB side)
        def endpoint(dev: str, port: str):
            if dev in objs:
                return objs[dev][port].id
            if port == prod_by_name[dev]["console"]:
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
            new = [devices[d["name"]].id for d in plan["devices"] if D["artifact_groups"].get(d["role"]) == g.name.value and devices[d["name"]].id not in have]
            for i in new:
                g.members.add(i)
            if new:
                await g.save()
            print(f"group {g.name.value:<14} +{len(new)}")

        await self.reconcile(site_name, plan, prod, keep=set(plan_addr))
