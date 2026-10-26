"""site_physical: racks, U positions, PDUs and cables for one site.

Reads the devices and interfaces fabric_site creates; never changes fabric_site's objects except
DcimDevice.rack / position / rack_face.
SITE_PHYSICAL_DRY_RUN=1 prints the plan and writes nothing.
"""
import contextvars
import ipaddress
import re
import math
import os

from infrahub_sdk.generator import InfrahubGenerator

DRY_RUN = os.environ.get("SITE_PHYSICAL_DRY_RUN", "0") == "1"

# Design values (P2-1xx: placement, PDUs, cabling) live in FabricDesign; load_values() fills D at the start of
# generate(); a missing or empty value stops the generator with the field name.
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

# ---------------------------------------------------------------- placement
NET_ROLES = ("spine", "border_leaf", "service_leaf", "bgw", "edge", "dci", "dmz", "isp_router", "cloud_router",
             "vpn_fw", "sdwan_fw", "internal_fw", "internet_fw")      # NET-A / NET-B racks: U = FabricDesign.<role>_rack_u
OOB_ROLES = ("console_server", "oob_access", "oob_agg", "oob_fw")             # placed only; their cables belong to oob_site
OOB_U_FIELD = {"console_server": "console_server", "oob_net": "oob_access", "oob_agg": "oob_agg", "oob_fw": "oob_fw"}

# ---------------------------------------------------------------- PDUs
PDU_ROLE = "pdu"
PDU_MGMT_ROLE = "management"
PDU_MGMT_DESCRIPTION = "OOB management"             # same text as switch mgmt0

# ---------------------------------------------------------------- cables
TIER_ROLES = ("spine", "border_leaf", "service_leaf", "bgw", "dci", "leaf", "edge", "dmz", "internal_fw", "internet_fw",
              "sdwan_fw", "vpn_fw", "isp_router", "cloud_router")  # cabled roles: tier = FabricDesign.<role>_cable_tier
CABLE_SKIP_IF_ROLES = ("management", "console")     # OOB home runs are oob_site's cables
SHORT_PORT = (("Ethernet", "Eth"), ("FourHundredGigE", "FH"))
KEEPALIVE_ROLE = "vpc_keepalive"
PEER_LINK_ROLE = "vpc_peer_link"
U_HEIGHT_M = 0.04445


async def load_design(client):
    nodes = await client.all(kind="FabricDesign")
    if len(nodes) != 1:
        raise ValueError(f"FabricDesign: expected exactly 1 object, found {len(nodes)}; fix it in Infrahub")
    return nodes[0]


def need(design, field):
    attr = getattr(design, field, None)
    value = attr.value if attr is not None else None
    if value is None:
        raise ValueError(f"FabricDesign.{field} is empty; fix it in Infrahub")
    return value


def load_values(design) -> None:
    """FabricDesign values of this generator (fills D)."""
    D.clear()
    D["net_rack"] = dict(need(design, "network_racks"))            # member 1 -> NET-A, member 2 -> NET-B
    D["net_u"] = {r: need(design, f"{r}_rack_u") for r in NET_ROLES}  # lowest U in NET-A / NET-B
    D["oob_rack"] = dict(need(design, "oob_racks"))
    D["oob_u"] = {k: need(design, f"{r}_rack_u") for k, r in OOB_U_FIELD.items()}
    D["bmc_u"] = need(design, "bmc_switch_rack_u")                  # rack of the first leaf it serves
    D["server_leaf_u"] = need(design, "leaf_rack_u")
    D["server_rows"] = list(need(design, "server_rows"))
    D["server_racks_per_row"] = need(design, "server_racks_per_row")
    D["device_face"] = need(design, "device_rack_face")
    D["pdu_face"] = need(design, "pdu_rack_face")                   # 0U, no position
    D["pdu_status"] = need(design, "status_pdu")
    D["pdu_mgmt_if"] = need(design, "pdu_mgmt_interface")
    D["pdu_type_60a"] = need(design, "pdu_type_60a")                # NET and growth racks
    D["pdu_type_30a"] = need(design, "pdu_type_30a")                # server, OOB, SAN racks
    D["pdu_60a_purposes"] = set(need(design, "pdu_60a_rack_purposes"))
    D["pdu_feeds"] = list(need(design, "pdu_feeds"))
    D["tier"] = {r: need(design, f"{r}_cable_tier") for r in TIER_ROLES}
    D["rack_width_m"] = float(need(design, "rack_width_m"))  # Text holding a decimal
    D["row_pitch_m"] = float(need(design, "row_pitch_m"))  # Text holding a decimal
    D["row_index"] = dict(need(design, "row_index"))
    D["vertical_per_end_m"] = float(need(design, "vertical_per_end_m"))  # Text holding a decimal
    D["dac_slack_m"] = float(need(design, "cable_slack_m"))  # Text holding a decimal
    D["stock_m"] = tuple(need(design, "cable_stock_lengths_m"))
    D["patch_racks"] = tuple(need(design, "network_patch_racks"))   # MDA
    D["patch_tie_break"] = need(design, "patch_rack_tie_break")     # equal tray runs go via this rack
    D["p2p_len"] = need(design, "p2p_link_prefix_length")           # routed point-to-point links (cable discovery)
    D["cable_same_rack"] = need(design, "cable_type_same_rack")
    D["cable_between_racks"] = need(design, "cable_type_between_racks")


def short_port(name: str) -> str:
    for full, short in SHORT_PORT:
        if name.startswith(full):
            return short + name[len(full):]
    return name


def to_stock(metres: float, what: str) -> int:
    for s in D["stock_m"]:
        if metres <= s:
            return s
    raise ValueError(f"{what}: {metres:.1f} m is longer than the longest stock length {D['stock_m'][-1]} m")


def rack_xy(rack: dict) -> tuple[float, float]:
    return (rack["row_position"] - 1) * D["rack_width_m"], D["row_index"][rack["row"]] * D["row_pitch_m"]


def tray(a: dict, b: dict) -> float:                # along the row, then across
    (ax, ay), (bx, by) = rack_xy(a), rack_xy(b)
    return abs(ax - bx) + abs(ay - by)


def place(site: str, name: str, role: str, description: str = "") -> tuple[str, int]:
    """(rack short name, lowest U) for a device of the site."""
    short = name.removeprefix(f"{site}-")
    if role == "oob_access" and "BMC" in short:
        m = re.search(rf"{site}-L(\d+)", description or "")
        if not m:
            raise ValueError(f"{name}: no served leaf in its description {description!r}")
        return place(site, f"{site}-L{m.group(1)}", "leaf")[0], D["bmc_u"]
    if role in OOB_ROLES:
        key = "oob_net" if role == "oob_access" else role
        member = short[-1]
        if member not in D["oob_rack"]:
            raise ValueError(f"{name}: no unit number 1/2 at the end of the name")
        return D["oob_rack"][member], D["oob_u"][key]
    if role == "leaf":
        n = int(short[1:])
        rows, per_row = D["server_rows"], D["server_racks_per_row"]
        i = min((n - 1) // per_row, len(rows) - 1)                   # rows filled in order; the last row takes the rest
        return f"{rows[i]}{n - i * per_row:02d}", D["server_leaf_u"]
    if role in D["net_u"]:
        member = short[-1]
        if member not in D["net_rack"]:
            raise ValueError(f"{name}: no member number 1/2 at the end of the name")
        return D["net_rack"][member], D["net_u"][role]
    raise ValueError(f"{name}: role {role} has no rack rule (decide it first)")


class SitePhysicalGenerator(InfrahubGenerator):
    async def upsert(self, kind: str, data: dict):
        obj = await self.client.create(kind=kind, data=data)
        await obj.save(allow_upsert=True)
        return obj

    async def set_device_physical(self, device_id: str, rack_id: str | None, position: int | None, face: str) -> None:
        """Raw mutation, not save(): fabric_site owns the device, so it must not enter this generator's
        tracking group (an untracked-next-run device would be deleted).  ❓ verify in the plan/branch test."""
        pos = "null" if position is None else str(position)
        rack = f'rack: {{id: "{rack_id}"}},' if rack_id else ""
        await self.client.execute_graphql(query=f"""
            mutation {{ DcimDeviceUpdate(data: {{id: "{device_id}", {rack}
                position: {{value: {pos}}}, rack_face: {{value: "{face}"}}}}) {{ ok }} }}""")

    async def generate(self, data: dict) -> None:
        site = data["LocationSite"]["edges"][0]["node"]["name"]["value"]
        load_values(await load_design(self.client))
        site_obj = await self.client.get(kind="LocationSite", name__value=site)

        # ---- racks of this site (objects/08-racks.yml)
        racks = {}
        for r in await self.client.filters(kind="LocationRack", parent__ids=[site_obj.id]):
            short = r.name.value.removeprefix(f"{site}-")
            racks[short] = {"obj": r, "name": r.name.value, "row": r.row.value, "row_position": r.row_position.value,
                            "purpose": r.purpose.value, "kw": r.power_budget_kw.value}
        if not racks:
            raise ValueError(f"{site}: no LocationRack objects; load objects/08-racks.yml first")

        # ---- devices made by fabric_site (role in the tier map), placed by rule
        devices = {}
        for d in await self.client.filters(kind="DcimDevice", location__name__value=site, include=["device_type"]):
            if d.role.value not in D["tier"] and d.role.value not in OOB_ROLES:
                continue
            rk, u = place(site, d.name.value, d.role.value, d.description.value or "")
            if rk not in racks:
                raise ValueError(f"{d.name.value}: rack {site}-{rk} does not exist")
            dtype = await self.client.get(kind="DcimDeviceType", id=d.device_type.id) if d.device_type.id else None
            devices[d.name.value] = {"obj": d, "role": d.role.value, "rack": rk, "u": u,
                                     "type": dtype.name.value if dtype else None}

        # ---- physical interfaces and their addresses
        ifs = {}
        for intf in await self.client.filters(kind="InterfacePhysical", include=["ip_addresses"],
                                              device__ids=[v["obj"].id for v in devices.values() if v["role"] in D["tier"]]):
            if intf.role.value in CABLE_SKIP_IF_ROLES:
                continue
            dev = next(n for n, v in devices.items() if v["obj"].id == intf.device.id)
            ifs[(dev, intf.name.value)] = {"obj": intf, "role": intf.role.value,
                                           "desc": intf.description.value or "", "addr_ids": [p.id for p in intf.ip_addresses.peers]}
        addr_ids = [a for v in ifs.values() for a in v["addr_ids"]]
        addr = {a.id: a.address.value for a in (await self.client.filters(kind="IpamIPAddress", ids=addr_ids) if addr_ids else [])}

        # ---- links: (1) shared p2p_link_prefix_length prefix, (2) same-named peer-link member inside a switch pair, (3) "DEVICE PORT" description
        links, seen = [], set()

        def add(a, b, why):
            key = frozenset((a, b))
            if a[0] == b[0] or key in seen:
                return
            seen.add(key); links.append((a, b, why))

        by_net = {}
        for k, v in ifs.items():
            for aid in v["addr_ids"]:
                iface = ipaddress.ip_interface(addr[aid])
                if iface.network.prefixlen == D["p2p_len"]:
                    by_net.setdefault(str(iface.network), []).append(k)
        for net, ends in by_net.items():
            if len(ends) == 2:
                add(ends[0], ends[1], f"/{D['p2p_len']} {net}")
        for pair in await self.client.filters(kind="FabricSwitchPair", include=["members"]):
            m = [next((n for n, v in devices.items() if v["obj"].id == p.id), None) for p in pair.members.peers]
            if None in m:
                continue
            for (dev, port), v in ifs.items():
                if dev == m[0] and v["role"] == PEER_LINK_ROLE and (m[1], port) in ifs:
                    add((m[0], port), (m[1], port), f"peer-link {pair.name.value}")
        for k, v in ifs.items():
            parts = v["desc"].split()
            if len(parts) == 2 and tuple(parts) in ifs:
                add(k, tuple(parts), "description")

        # ---- cable rows
        cables = []
        for a, b, why in links:
            if D["tier"][devices[a[0]]["role"]] > D["tier"][devices[b[0]]["role"]]:       # upstream first
                a, b = b, a
            da, db = devices[a[0]], devices[b[0]]
            label = f"{a[0]}:{short_port(a[1])}-{b[0]}:{short_port(b[1])}"
            if da["rack"] == db["rack"]:
                ctype = D["cable_same_rack"]
                length = to_stock(abs(da["u"] - db["u"]) * U_HEIGHT_M + D["dac_slack_m"], label)
            else:
                ctype = D["cable_between_racks"]
                ra, rb = racks[da["rack"]], racks[db["rack"]]
                # same key as transforms/site_cable_schedule.py: rounded tray run, ties to patch_rack_tie_break
                via = min(D["patch_racks"], key=lambda p: (round(tray(ra, racks[p]) + tray(racks[p], rb), 3), p != D["patch_tie_break"]))
                run = tray(ra, racks[via]) + tray(racks[via], rb) + 2 * D["vertical_per_end_m"]
                length = to_stock(run, label)
            cables.append({"label": label, "type": ctype, "length": length, "ends": (a, b), "why": why})

        # ---- PDUs: two per powered rack
        pdus = []
        for short, r in sorted(racks.items()):
            if r["kw"]:
                ptype = D["pdu_type_60a"] if r["purpose"] in D["pdu_60a_purposes"] else D["pdu_type_30a"]
                for feed in D["pdu_feeds"]:
                    pdus.append({"name": f"{r['name']}-PDU-{feed}", "rack": short, "type": ptype})

        # ---- plan
        print(f"=== {site}: {len(racks)} racks, {len(devices)} devices placed, {len(pdus)} PDUs, {len(cables)} cables")
        for n, v in sorted(devices.items()):
            print(f"  PLACE  {n:<16} {site}-{v['rack']}  U{v['u']:<3} {D['device_face']}")
        for p in pdus:
            print(f"  PDU    {p['name']:<22} {p['type']}")
        for c in sorted(cables, key=lambda c: c["label"]):
            print(f"  CABLE  {c['label']:<44} {c['type']:<4} {c['length']:>2} m  ({c['why']})")
        if DRY_RUN:
            print("DRY RUN: nothing written")
            return

        # ---- writes
        for n, v in devices.items():
            await self.set_device_physical(v["obj"].id, racks[v["rack"]]["obj"].id, v["u"], D["device_face"])
        types = {t: await self.client.get(kind="DcimDeviceType", name__value=t) for t in {p["type"] for p in pdus}}
        for p in pdus:
            pdu = await self.upsert("DcimDevice", {"name": p["name"], "role": PDU_ROLE, "status": D["pdu_status"],
                                                   "location": site_obj.id, "device_type": types[p["type"]].id})
            await self.set_device_physical(pdu.id, racks[p["rack"]]["obj"].id, None, D["pdu_face"])
            await self.upsert("InterfacePhysical", {"name": D["pdu_mgmt_if"], "device": pdu.id, "role": PDU_MGMT_ROLE,
                                                    "description": PDU_MGMT_DESCRIPTION, "status": D["pdu_status"]})
        for c in cables:
            await self.upsert("DcimCable", {"name": c["label"], "cable_type": c["type"], "length_m": c["length"],
                                            "connected_endpoints": [ifs[e]["obj"].id for e in c["ends"]]})
        print(f"WROTE {len(devices)} placements, {len(pdus)} PDUs, {len(cables)} cables")
