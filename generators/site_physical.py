"""site_physical: racks, U positions, PDUs and cables for one site.

Reads the devices and interfaces fabric_site creates; never changes fabric_site's objects except
DcimDevice.rack / position / rack_face (P2-186, P2-195). Every constant traces to decisions.xlsx (P2-nnn).
SITE_PHYSICAL_DRY_RUN=1 prints the plan and writes nothing.
"""
import ipaddress
import re
import math
import os

from infrahub_sdk.generator import InfrahubGenerator

DRY_RUN = os.environ.get("SITE_PHYSICAL_DRY_RUN", "0") == "1"

# ---------------------------------------------------------------- placement (P2-116, P2-126, P2-145, P2-189)
NET_RACK = {"1": "B04", "2": "B08"}                 # member 1 -> NET-A, member 2 -> NET-B
NET_U = {                                           # lowest U in NET-A / NET-B (P2-126)
    "spine": 34, "border_leaf": 32, "service_leaf": 30, "bgw": 28, "edge": 26, "dci": 24,
    "dmz": 22, "isp_router": 20, "cloud_router": 18, "vpn_fw": 12, "sdwan_fw": 9, "internal_fw": 5, "internet_fw": 1,
}                                                   # cloud_router, vpn_fw, sdwan_fw: P2-126 via P2-235
OOB_RACK = {"1": "B03", "2": "B09"}                 # P2-132: unit 1 -> B03, unit 2 -> B09
OOB_U = {"console_server": 35, "oob_net": 33, "oob_agg": 31, "oob_fw": 29}   # P2-144
BMC_U = 40                                          # P2-145: BMC switch in the rack of the first leaf it serves (P2-146)
OOB_ROLES = ("console_server", "oob_access", "oob_agg", "oob_fw")             # placed only; their cables belong to oob_site
SERVER_LEAF_U = 41                                  # P2-145
DEVICE_FACE = "front"                               # P2-189
PDU_FACE = "rear"                                   # P2-160 (0U, no position)

# ---------------------------------------------------------------- PDUs (P2-157 to P2-161, P2-169, P2-197 to P2-199)
PDU_ROLE = "pdu"
PDU_STATUS = "provisioning"
PDU_MGMT_IF = "mgmt0"
PDU_MGMT_ROLE = "management"
PDU_MGMT_DESCRIPTION = "OOB management"             # same text as switch mgmt0 (Day 6)
PDU_TYPE_60A = "PDU 415V 3P 60A switched"          # NET and growth racks (P2-141, P2-188)
PDU_TYPE_30A = "PDU 415V 3P 30A switched"          # server, OOB, SAN racks
PDU_60A_PURPOSES = {"network", "growth"}

# ---------------------------------------------------------------- cables (P2-151, P2-153, P2-167 to P2-183, P2-187, P2-191 to P2-196)
TIER = {"spine": 0, "border_leaf": 1, "service_leaf": 1, "bgw": 1, "dci": 1, "leaf": 2,
        "edge": 3, "dmz": 3, "internal_fw": 4, "internet_fw": 4, "sdwan_fw": 4, "vpn_fw": 4,
        "isp_router": 5, "cloud_router": 5}                                                  # P2-191 (+ Phase 3/4 roles, P2-235)
CABLE_SKIP_IF_ROLES = ("management", "console")     # OOB home runs are oob_site's cables (P2-231)
SHORT_PORT = (("Ethernet", "Eth"), ("FourHundredGigE", "FH"))                               # P2-172, P2-192
HUNDRED_G_TYPES = {"N9K-C93180YC-FX3"}             # 6 x 100G uplinks/peer-link (design, platforms)
KEEPALIVE_ROLE = "vpc_keepalive"
PEER_LINK_ROLE = "vpc_peer_link"
RACK_WIDTH_M = 0.8                                  # P2-178
ROW_PITCH_M = 3.0                                   # P2-179
ROW_INDEX = {"A": 0, "B": 1, "C": 2, "D": 3}
VERTICAL_PER_END_M = 3.0                            # P2-180
DAC_SLACK_M = 1.0                                   # P2-196
U_HEIGHT_M = 0.04445
STOCK_M = (1, 2, 3, 5, 7, 10, 15, 20, 30)           # P2-181
NETWORK_PATCH_RACKS = ("B05", "B06", "B07")         # P2-182 (MDA)


def short_port(name: str) -> str:
    for full, short in SHORT_PORT:
        if name.startswith(full):
            return short + name[len(full):]
    return name


def to_stock(metres: float, what: str) -> int:
    for s in STOCK_M:
        if metres <= s:
            return s
    raise ValueError(f"{what}: {metres:.1f} m is longer than the longest stock length {STOCK_M[-1]} m")


def rack_xy(rack: dict) -> tuple[float, float]:
    return (rack["row_position"] - 1) * RACK_WIDTH_M, ROW_INDEX[rack["row"]] * ROW_PITCH_M


def tray(a: dict, b: dict) -> float:                # P2-194: along the row, then across
    (ax, ay), (bx, by) = rack_xy(a), rack_xy(b)
    return abs(ax - bx) + abs(ay - by)


def place(site: str, name: str, role: str, description: str = "") -> tuple[str, int]:
    """(rack short name, lowest U) for a device of the site (P2-126, P2-132, P2-144 to P2-146, P2-235)."""
    short = name.removeprefix(f"{site}-")
    if role == "oob_access" and "BMC" in short:
        m = re.search(rf"{site}-L(\d+)", description or "")
        if not m:
            raise ValueError(f"{name}: no served leaf in its description {description!r}")
        return place(site, f"{site}-L{m.group(1)}", "leaf")[0], BMC_U
    if role in OOB_ROLES:
        key = "oob_net" if role == "oob_access" else role
        member = short[-1]
        if member not in OOB_RACK:
            raise ValueError(f"{name}: no unit number 1/2 at the end of the name")
        return OOB_RACK[member], OOB_U[key]
    if role == "leaf":
        n = int(short[1:])
        return (f"A{n:02d}" if n <= 10 else f"C{n - 10:02d}"), SERVER_LEAF_U
    if role in NET_U:
        member = short[-1]
        if member not in NET_RACK:
            raise ValueError(f"{name}: no member number 1/2 at the end of the name")
        return NET_RACK[member], NET_U[role]
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
        site_obj = await self.client.get(kind="LocationSite", name__value=site)

        # ---- racks of this site (objects/08-racks.yml, P2-185)
        racks = {}
        for r in await self.client.filters(kind="LocationRack", parent__ids=[site_obj.id]):
            short = r.name.value.removeprefix(f"{site}-")
            racks[short] = {"obj": r, "name": r.name.value, "row": r.row.value, "row_position": r.row_position.value,
                            "purpose": r.purpose.value, "kw": r.power_budget_kw.value}
        if not racks:
            raise ValueError(f"{site}: no LocationRack objects; load objects/08-racks.yml first")

        # ---- devices made by fabric_site (role in TIER), placed by rule
        devices = {}
        for d in await self.client.filters(kind="DcimDevice", location__name__value=site, include=["device_type"]):
            if d.role.value not in TIER and d.role.value not in OOB_ROLES:
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
                                              device__ids=[v["obj"].id for v in devices.values() if v["role"] in TIER]):
            if intf.role.value in CABLE_SKIP_IF_ROLES:
                continue
            dev = next(n for n, v in devices.items() if v["obj"].id == intf.device.id)
            ifs[(dev, intf.name.value)] = {"obj": intf, "role": intf.role.value,
                                           "desc": intf.description.value or "", "addr_ids": [p.id for p in intf.ip_addresses.peers]}
        addr_ids = [a for v in ifs.values() for a in v["addr_ids"]]
        addr = {a.id: a.address.value for a in (await self.client.filters(kind="IpamIPAddress", ids=addr_ids) if addr_ids else [])}

        # ---- links: (1) shared /31, (2) same-named peer-link member inside a switch pair, (3) "DEVICE PORT" description
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
                if iface.network.prefixlen == 31:
                    by_net.setdefault(str(iface.network), []).append(k)
        for net, ends in by_net.items():
            if len(ends) == 2:
                add(ends[0], ends[1], f"/31 {net}")
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
            if TIER[devices[a[0]]["role"]] > TIER[devices[b[0]]["role"]]:       # upstream first (P2-171, P2-191)
                a, b = b, a
            da, db = devices[a[0]], devices[b[0]]
            label = f"{a[0]}:{short_port(a[1])}-{b[0]}:{short_port(b[1])}"
            if da["rack"] == db["rack"]:
                ctype = "dac"                                                      # P2-174
                length = to_stock(abs(da["u"] - db["u"]) * U_HEIGHT_M + DAC_SLACK_M, label)   # P2-196
            else:
                ctype = "smf"                                                      # P2-175, P2-177, P2-193
                ra, rb = racks[da["rack"]], racks[db["rack"]]
                via = min(NETWORK_PATCH_RACKS, key=lambda p: tray(ra, racks[p]) + tray(racks[p], rb))
                run = tray(ra, racks[via]) + tray(racks[via], rb) + 2 * VERTICAL_PER_END_M          # P2-182
                length = to_stock(run, label)
            cables.append({"label": label, "type": ctype, "length": length, "ends": (a, b), "why": why})

        # ---- PDUs: two per powered rack (P2-157)
        pdus = []
        for short, r in sorted(racks.items()):
            if r["kw"]:
                ptype = PDU_TYPE_60A if r["purpose"] in PDU_60A_PURPOSES else PDU_TYPE_30A
                for feed in ("A", "B"):
                    pdus.append({"name": f"{r['name']}-PDU-{feed}", "rack": short, "type": ptype})

        # ---- plan
        print(f"=== {site}: {len(racks)} racks, {len(devices)} devices placed, {len(pdus)} PDUs, {len(cables)} cables")
        for n, v in sorted(devices.items()):
            print(f"  PLACE  {n:<16} {site}-{v['rack']}  U{v['u']:<3} {DEVICE_FACE}")
        for p in pdus:
            print(f"  PDU    {p['name']:<22} {p['type']}")
        for c in sorted(cables, key=lambda c: c["label"]):
            print(f"  CABLE  {c['label']:<44} {c['type']:<4} {c['length']:>2} m  ({c['why']})")
        if DRY_RUN:
            print("DRY RUN: nothing written")
            return

        # ---- writes
        for n, v in devices.items():
            await self.set_device_physical(v["obj"].id, racks[v["rack"]]["obj"].id, v["u"], DEVICE_FACE)
        types = {t: await self.client.get(kind="DcimDeviceType", name__value=t) for t in {p["type"] for p in pdus}}
        for p in pdus:
            pdu = await self.upsert("DcimDevice", {"name": p["name"], "role": PDU_ROLE, "status": PDU_STATUS,
                                                   "location": site_obj.id, "device_type": types[p["type"]].id})
            await self.set_device_physical(pdu.id, racks[p["rack"]]["obj"].id, None, PDU_FACE)
            await self.upsert("InterfacePhysical", {"name": PDU_MGMT_IF, "device": pdu.id, "role": PDU_MGMT_ROLE,
                                                    "description": PDU_MGMT_DESCRIPTION, "status": PDU_STATUS})
        for c in cables:
            await self.upsert("DcimCable", {"name": c["label"], "cable_type": c["type"], "length_m": c["length"],
                                            "connected_endpoints": [ifs[e]["obj"].id for e in c["ends"]]})
        print(f"WROTE {len(devices)} placements, {len(pdus)} PDUs, {len(cables)} cables")
