"""site_diagrams: one .drawio file per site, one tab per view.

Tabs: Physical, Underlay, Overlay, VRF, Path, Racks.
Every value comes from the query.
"""
from html import escape
import ipaddress

from infrahub_sdk.transforms import InfrahubTransform

# draw.io built-in fill/stroke pairs per role
COLOURS = {
    "spine": ("#1ba1e2", "#006eaf"),
    "border_leaf": ("#dae8fc", "#6c8ebf"), "service_leaf": ("#dae8fc", "#6c8ebf"),
    "bgw": ("#dae8fc", "#6c8ebf"), "dci": ("#dae8fc", "#6c8ebf"),
    "leaf": ("#d5e8d4", "#82b366"),
    "edge": ("#fff2cc", "#d6b656"), "dmz": ("#fff2cc", "#d6b656"),
    "internal_fw": ("#f8cecc", "#b85450"), "internet_fw": ("#f8cecc", "#b85450"),
    "isp_router": ("#e1d5e7", "#9673a6"), "cloud_router": ("#e1d5e7", "#9673a6"),
    "sdwan_fw": ("#f8cecc", "#b85450"), "vpn_fw": ("#f8cecc", "#b85450"),
    "oob_fw": ("#ffe6cc", "#d79b00"), "oob_agg": ("#ffe6cc", "#d79b00"),
    "oob_access": ("#ffe6cc", "#d79b00"), "console_server": ("#ffe6cc", "#d79b00"),
    "pdu": ("#f5f5f5", "#666666"),
}
ROLE_LABEL = {"spine": "Spine", "border_leaf": "Border leaf", "service_leaf": "Service leaf", "bgw": "Border gateway",
              "dci": "DCI", "leaf": "Server leaf", "edge": "Edge", "dmz": "DMZ", "internal_fw": "Internal FW",
              "internet_fw": "Internet FW", "isp_router": "ISP router", "pdu": "PDU",
              "cloud_router": "Cloud router", "sdwan_fw": "SD-WAN FW", "vpn_fw": "VPN-partner FW", "oob_fw": "OOB FW",
              "oob_agg": "OOB aggregation", "oob_access": "OOB access", "console_server": "Console server"}
RIGHT_COLUMN = ("border_leaf", "service_leaf", "bgw", "dci", "edge", "dmz", "internal_fw", "internet_fw", "isp_router")
LINE_WIDTH = {"dac": 2, "smf": 2}   # speed by thickness: 400G 3, 100G 2, 10/25G 1, from DcimDeviceType.uplink_speed_100g
# Design values read at the start of transform() (FabricDesign: vtep_roles, transit_vrf, attachment hosts and length, console
# cable type, routed link length, underlay area, PDU rack face; DcimDeviceType: height, uplink_speed_100g, vpc_keepalive_port).
# Missing FabricDesign values stop the transform; a missing height / uplink_speed_100g stops it where it is used.
DESIGN_FIELDS = ("vtep_roles", "transit_vrf", "attachment_leaf_hosts", "attachment_device_host", "attachment_prefix_length",
                 "oob_cable_type_console", "p2p_link_prefix_length", "underlay_ospf_area", "pdu_rack_face")
DESIGN: dict = {}


def configure(data: dict) -> None:
    fd = data.get("FabricDesign") or {}
    edges = fd.get("edges") or []
    if fd.get("count") != 1 or len(edges) != 1:
        raise ValueError(f"FabricDesign: expected exactly 1 object, found {fd.get('count', len(edges))}; fix it in Infrahub "
                         "(and check that the query asks for FabricDesign)")
    DESIGN.clear()
    for f in DESIGN_FIELDS:
        value = (edges[0]["node"].get(f) or {}).get("value")
        if value is None:
            raise ValueError(f"FabricDesign.{f} is empty; fix it in Infrahub")
        DESIGN[f] = value
BOX_W, BOX_H, GAP = 120, 46, 24
U_PX = 14                            # Racks tab: pixels per U
RACK_W = 190


SHORT_PORT = (("Ethernet", "Eth"), ("FourHundredGigE", "FH"))


def short_port(name: str) -> str:
    for full, short in SHORT_PORT:
        if name.startswith(full):
            return short + name[len(full):]
    return name


def type_value(devices: dict, name: str, key: str, field: str):
    """A DcimDeviceType value of a device, failing loudly when it is missing (test is None: 0U PDUs keep 0)."""
    value = devices[name][key]
    if value is None:
        raise ValueError(f"{name}: DcimDeviceType {devices[name]['type']}.{field} is empty; fix it in Infrahub")
    return value


def speed_100g(devices: dict, name: str) -> bool:
    return type_value(devices, name, "100g", "uplink_speed_100g") is True


def natural(name: str):
    import re
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


class Page:
    def __init__(self, name: str):
        self.name, self.cells, self.n = name, [], 2

    def _id(self) -> str:
        self.n += 1
        return f"c{self.n}"

    def box(self, x, y, w, h, text, fill="#ffffff", stroke="#000000", extra="") -> str:
        i = self._id()
        style = f"rounded=1;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};fontSize=11;{extra}"
        self.cells.append(f'<mxCell id="{i}" value="{escape(text)}" style="{style}" vertex="1" parent="1">'
                          f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        return i

    def text(self, x, y, w, h, text, size=12, bold=False) -> None:
        style = f"text;html=1;align=left;verticalAlign=middle;fontSize={size};" + ("fontStyle=1;" if bold else "")
        self.cells.append(f'<mxCell id="{self._id()}" value="{escape(text)}" style="{style}" vertex="1" parent="1">'
                          f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')

    def edge(self, src, dst, width=1, dashed=False, src_label="", dst_label="", label="",
             exit_=None, entry=None, vertical_labels=False, straight=False) -> None:
        i = self._id()
        style = f"endArrow=none;html=1;strokeWidth={width};" + ("dashed=1;" if dashed else "")
        if exit_ or entry:
            style += "edgeStyle=none;" if straight else "edgeStyle=orthogonalEdgeStyle;rounded=0;"
            if exit_:
                style += f"exitX={exit_[0]:.4f};exitY={exit_[1]};exitDx=0;exitDy=0;"
            if entry:
                style += f"entryX={entry[0]:.4f};entryY={entry[1]};entryDx=0;entryDy=0;"
        else:
            style += "edgeStyle=none;"
        self.cells.append(f'<mxCell id="{i}" value="{escape(label)}" style="{style}fontSize=9;" edge="1" parent="1" source="{src}" target="{dst}">'
                          f'<mxGeometry relative="1" as="geometry"/></mxCell>')
        rot = "horizontal=0;" if vertical_labels else ""
        for pos, lab in ((-0.7, src_label), (0.7, dst_label)):
            if lab:
                self.cells.append(f'<mxCell id="{self._id()}" value="{escape(lab)}" style="edgeLabel;html=1;fontSize=9;labelBackgroundColor=#ffffff;{rot}" '
                                  f'vertex="1" connectable="0" parent="{i}"><mxGeometry x="{pos}" relative="1" as="geometry"><mxPoint as="offset"/></mxGeometry></mxCell>')

    def xml(self) -> str:
        return (f'<diagram name="{escape(self.name)}" id="{escape(self.name.lower())}"><mxGraphModel grid="1" gridSize="10" page="0">'
                f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{"".join(self.cells)}</root></mxGraphModel></diagram>')


class SiteDiagrams(InfrahubTransform):
    query = "site_diagrams"

    async def transform(self, data: dict) -> str:
        configure(data)
        devices = {}
        if_owner, if_name, if_addr = {}, {}, {}
        for e in data["DcimDevice"]["edges"]:
            d = e["node"]
            name = d["name"]["value"]
            role = d["role"]["value"]
            dt = (d.get("device_type") or {}).get("node") or {}
            devices[name] = {
                "role": role, "type": (dt.get("name") or {}).get("value"), "height": (dt.get("height") or {}).get("value"),
                "rack": (((d.get("rack") or {}).get("node") or {}).get("name") or {}).get("value"),
                "u": d["position"]["value"], "asn": (((d.get("asn") or {}).get("node") or {}).get("asn") or {}).get("value"),
                "lo0": None, "lo1": None,
                "100g": (dt.get("uplink_speed_100g") or {}).get("value"),
                "keepalive": (dt.get("vpc_keepalive_port") or {}).get("value"),
            }
            for ie in d["interfaces"]["edges"]:
                intf = ie["node"]
                iname = intf["name"]["value"]
                addrs = [a["node"]["address"]["value"] for a in (intf.get("ip_addresses") or {}).get("edges", [])]
                if_owner[intf["id"]], if_name[intf["id"]] = name, iname
                if_addr[intf["id"]] = addrs
                if iname == "loopback0" and addrs:
                    devices[name]["lo0"] = addrs[0]
                if iname == "loopback1" and addrs:
                    devices[name]["lo1"] = addrs[0]
        site = next(iter(devices)).split("-")[0] if devices else "SITE"
        DESIGN["keepalive_ports"] = tuple(sorted({d["keepalive"] for d in devices.values() if d["keepalive"]}))
        cables = []
        for e in data["DcimCable"]["edges"]:
            c = e["node"]
            ends = [x["node"]["id"] for x in c["connected_endpoints"]["edges"]]
            if len(ends) == 2 and all(x in if_owner for x in ends):
                cables.append({"label": c["name"]["value"], "type": c["cable_type"]["value"], "ends": ends})

        pages = [self.physical(devices, cables, if_owner, if_name),
                 self.underlay(devices, cables, if_owner, if_name, if_addr),
                 self.overlay(devices),
                 self.vrf(devices, data),
                 self.path(devices, cables, if_owner, if_name, if_addr, data),
                 self.racks(site, devices, data),
                 self.oob(devices, cables, if_owner, if_name)]
        return '<mxfile host="infrahub">' + "".join(p.xml() for p in pages) + "</mxfile>"

    # ------------------------------------------------------------ layout
    # S1 is a wide bar ABOVE the spine-attached row, S2 a wide bar BELOW it; every spine link drops straight to its
    # device, so each port label has its own spot. Other roles (edge, DCI, DMZ, FW, ISP) sit in a block to the right.
    ROW_ROLES = ("leaf", "border_leaf", "service_leaf", "bgw")
    SLOT_W, ROW_Y, BAR_H = 200, 330, 34
    RIGHT_ORDER = ("isp_router", "cloud_router", "edge", "internet_fw", "sdwan_fw", "vpn_fw", "dmz", "internal_fw", "dci")
    RIGHT_W, RIGHT_GAP, RIGHT_ROW = 150, 170, 120

    def layout(self, page: Page, devices: dict, extra=lambda n, d: "") -> dict:
        ids, geo = {}, {}
        row = sorted([n for n, d in devices.items() if d["role"] in self.ROW_ROLES],
                     key=lambda n: (self.ROW_ROLES.index(devices[n]["role"]), natural(n)))
        spines = sorted([n for n, d in devices.items() if d["role"] == "spine"], key=natural)
        others = [n for n, d in devices.items() if d["role"] in self.RIGHT_ORDER and n not in row]
        width = max(len(row), 1) * self.SLOT_W
        box_h = BOX_H + 14
        for i, n in enumerate(row):
            x = i * self.SLOT_W + (self.SLOT_W - BOX_W) / 2
            ids[n] = page.box(x, self.ROW_Y, BOX_W, box_h, self.lab(n, devices[n], extra), *COLOURS[devices[n]["role"]])
            geo[n] = (x, BOX_W)
        bar_y = {0: self.ROW_Y - 230, 1: self.ROW_Y + box_h + 196}
        for k, n in enumerate(spines[:2]):
            ids[n] = page.box(0, bar_y[k], width, self.BAR_H, self.lab(n, devices[n], extra).replace("\n", "  |  "),
                              *COLOURS["spine"], extra="fontColor=#ffffff;rounded=0;")
            geo[n] = (0, width)
        ox = width + 140
        box = {n: (x, self.ROW_Y, BOX_W, box_h) for n, (x, _) in geo.items() if n not in spines}
        roles_present = [r for r in self.RIGHT_ORDER if any(devices[n]["role"] == r for n in others)]
        for n in others:
            r = roles_present.index(devices[n]["role"])
            member = 0 if n.endswith("1") else 1
            x, y = ox + member * (self.RIGHT_W + self.RIGHT_GAP), 60 + r * self.RIGHT_ROW
            ids[n] = page.box(x, y, self.RIGHT_W, BOX_H, self.lab(n, devices[n], extra), *COLOURS[devices[n]["role"]])
            box[n] = (x, y, self.RIGHT_W, BOX_H)
        self._box = box
        y = bar_y[1] + 80
        for role, (fill, stroke) in COLOURS.items():          # legend on every tab: roles drawn on this tab
            if any(devices[n]["role"] == role for n in ids):
                page.box(0, y, 18, 14, "", fill, stroke)
                page.text(24, y - 3, 140, 20, ROLE_LABEL[role], size=10)
                y += 22
        self._geo, self._spines = geo, spines[:2]
        return ids

    def spine_drop(self, page, ids, spine, dev, k, n, width, src_label, dst_label):
        """Straight vertical link from a spine bar to a row device: link k of n per spine on that device."""
        top = spine == self._spines[0]
        x, w = self._geo[dev]
        frac_dev = (0.12 + 0.76 * (k + 0.5) / n) if n else 0.5
        x_abs = x + w * frac_dev
        bar_x, bar_w = self._geo[spine]
        page.edge(ids[spine], ids[dev], width, src_label=src_label, dst_label=dst_label, vertical_labels=True,
                  exit_=((x_abs - bar_x) / bar_w, 1 if top else 0), entry=(frac_dev, 0 if top else 1))

    @staticmethod
    def lab(name, d, extra) -> str:
        more = extra(name, d)
        return f"{name}\n{ROLE_LABEL[d['role']]}" + (f"\n{more}" if more else "")

    def width_for(self, devices, a, b) -> int:
        return 2 if speed_100g(devices, a) or speed_100g(devices, b) else 3

    def draw_links(self, page, ids, devices, links, width_of, label_of):
        """links: (end_a, end_b) interface ids. Spine links become straight drops; the rest plain edges."""
        per = {}
        for a, b in links:
            for s_end, d_end in ((a, b), (b, a)):
                s_dev, d_dev = self.if_owner[s_end], self.if_owner[d_end]
                if s_dev in self._spines and d_dev in self._geo and d_dev not in self._spines:
                    per.setdefault((s_dev, d_dev), []).append((s_end, d_end))
        done = set()
        for (spine, dev), ends in per.items():
            ends.sort(key=lambda e: natural(self.if_name[e[1]]))
            for k, (s_end, d_end) in enumerate(ends):
                self.spine_drop(page, ids, spine, dev, k, len(ends), width_of(s_end, d_end), *label_of(s_end, d_end))
                done.add(frozenset((s_end, d_end)))
        pairs = {}                                   # every cable of a device pair as its own offset line
        for a, b in links:
            if frozenset((a, b)) in done:
                continue
            da, db = self.if_owner[a], self.if_owner[b]
            if da in ids and db in ids:
                pairs.setdefault(frozenset((da, db)), []).append((a, b))
        for key, ends in pairs.items():
            ends.sort(key=lambda e: natural(self.if_name[e[0]]))
            n = len(ends)
            for k, (a, b) in enumerate(ends):
                da, db = self.if_owner[a], self.if_owner[b]
                f = 0.5 if n == 1 else 0.15 + 0.7 * k / (n - 1)
                ba, bb = self._box.get(da), self._box.get(db)
                la, lb = label_of(a, b)
                if ba and bb and abs(ba[1] - bb[1]) < 1:          # side by side: right side -> left side
                    if ba[0] > bb[0]:
                        da, db, la, lb = db, da, lb, la
                    page.edge(ids[da], ids[db], width_of(a, b), src_label=la, dst_label=lb,
                              exit_=(1, f), entry=(0, f), straight=True)
                elif ba and bb:                                    # different rows: bottom of upper -> top of lower
                    if ba[1] > bb[1]:
                        da, db, la, lb = db, da, lb, la
                        ba, bb = bb, ba
                    if n == 1:                                     # lean toward the other box so ends never share a point
                        side = (bb[0] > ba[0] + 1) - (bb[0] < ba[0] - 1)
                        fx, fy = {1: (0.75, 0.25), -1: (0.25, 0.75), 0: (0.4, 0.4)}[side]
                    else:
                        fx = fy = f
                    page.edge(ids[da], ids[db], width_of(a, b), src_label=la, dst_label=lb,
                              exit_=(fx, 1), entry=(fy, 0), straight=True)
                else:
                    page.edge(ids[da], ids[db], width_of(a, b), src_label=la, dst_label=lb)

    # ------------------------------------------------------------ Physical
    def physical(self, devices, cables, if_owner, if_name) -> Page:
        p = Page("Physical")
        p.text(0, 10, 900, 30, "Physical: every cable; port at each end (short names); line width = speed", 14, True)
        self.if_owner, self.if_name = if_owner, if_name
        ids = self.layout(p, devices)
        def width_of(a, b):
            n = if_name[a]
            return 1 if DESIGN["keepalive_ports"] and n.endswith(DESIGN["keepalive_ports"]) else self.width_for(devices, if_owner[a], if_owner[b])
        self.draw_links(p, ids, devices, [tuple(c["ends"]) for c in cables], width_of,
                        lambda a, b: (short_port(if_name[a]), short_port(if_name[b])))
        return p

    # ------------------------------------------------------------ Underlay
    def underlay(self, devices, cables, if_owner, if_name, if_addr) -> Page:
        p = Page("Underlay")
        area = int(ipaddress.ip_address(str(DESIGN["underlay_ospf_area"])))      # dotted area id -> number (0.0.0.0 -> 0)
        p.text(0, 10, 900, 30, f"Underlay: OSPF area {area}, /{DESIGN['p2p_link_prefix_length']} on every routed link, loopback0 per device", 14, True)
        self.if_owner, self.if_name = if_owner, if_name
        ids = self.layout(p, devices, extra=lambda n, d: f"lo0 {d['lo0']}" if d["lo0"] else "")
        def p31(i):
            return [x for x in if_addr.get(i, []) if ipaddress.ip_interface(x).network.prefixlen == DESIGN["p2p_link_prefix_length"]]
        links = [tuple(c["ends"]) for c in cables if p31(c["ends"][0]) and p31(c["ends"][1])]
        self.draw_links(p, ids, devices, links, lambda a, b: 1, lambda a, b: (p31(a)[0], p31(b)[0]))
        return p

    # ------------------------------------------------------------ Overlay
    def overlay(self, devices) -> Page:
        p = Page("Overlay")
        p.text(0, 10, 900, 30, "Overlay: iBGP L2VPN EVPN to the spine route reflectors; VTEP = loopback1", 14, True)
        vtep = {n: d for n, d in devices.items() if d["role"] in ("spine",) + tuple(DESIGN["vtep_roles"])}
        ids = self.layout(p, vtep, extra=lambda n, d: f"VTEP {d['lo1']}" if d["lo1"] else (f"RR {d['lo0']}" if d["role"] == "spine" else ""))
        for n, d in vtep.items():
            if d["role"] != "spine" and d["asn"] and n in self._geo:
                for s in self._spines:
                    if devices[s]["asn"] == d["asn"]:
                        self.spine_drop(p, ids, s, n, 0, 1, 1, "", "")
        return p

    # ------------------------------------------------------------ VRF: one band per VRF
    @staticmethod
    def attachments(data):
        out = []
        for e in (data.get("FabricAttachment") or {}).get("edges", []):
            a = e["node"]
            sp = (a.get("switch_pair") or {}).get("node") or {}
            out.append({"name": a["name"]["value"], "vlan": a["vlan_id"]["value"],
                        "vrf": a["vrf"]["node"]["name"]["value"], "prefix": a["prefix"]["node"]["prefix"]["value"],
                        "pair": (sp.get("name") or {}).get("value"),
                        "members": [m["node"]["name"]["value"] for m in (sp.get("members") or {}).get("edges", [])],
                        "fws": [(d["node"]["name"]["value"], (((d["node"].get("asn") or {}).get("node") or {}).get("asn") or {}).get("value"))
                                for d in a["devices"]["edges"]]})
        return out

    def vrf(self, devices, data) -> Page:
        p = Page("VRF")
        p.text(0, 10, 900, 30, f"VRF: one band per VRF; attachments = leaf pair <-> firewall pair, /{DESIGN['attachment_prefix_length']}, VLAN, eBGP peer ASN", 14, True)
        hosts = "/".join(f".{h}" for h in DESIGN["attachment_leaf_hosts"]) + f" leaves, .{DESIGN['attachment_device_host']} FW"
        vrfs = sorted(((e["node"]["name"]["value"], e["node"]["l3_vni"]["value"], e["node"]["segments"]["count"])
                       for e in (data.get("FabricVRF") or {}).get("edges", [])), key=lambda v: (v[0] != DESIGN["transit_vrf"], v[0]))
        att = self.attachments(data)
        y = 60
        for name, vni, nseg in vrfs:
            mine = [a for a in att if a["vrf"] == name]
            band_h = max(1, len(mine)) * (BOX_H + 16) + 50
            p.box(0, y, 1100, band_h, "", "#ffffff", "#cccccc", extra="rounded=0;")
            p.text(10, y + 6, 600, 20, f"VRF {name} | L3VNI {vni} | segments {nseg}" + ("  (hub)" if name == DESIGN["transit_vrf"] else ""), 12, True)
            for i, a in enumerate(mine):
                yy = y + 36 + i * (BOX_H + 16)
                role = devices.get(a["members"][0], {}).get("role", "service_leaf") if a["members"] else "service_leaf"
                left = p.box(20, yy, 260, BOX_H, f"{a['pair']}\n{', '.join(a['members'])}", *COLOURS.get(role, COLOURS["service_leaf"]))
                fw_role = devices.get(a["fws"][0][0], {}).get("role", "internal_fw") if a["fws"] else "internal_fw"
                fw_txt = ", ".join(n for n, _ in a["fws"]) + (f"\nAS {a['fws'][0][1]}" if a["fws"] and a["fws"][0][1] else "")
                right = p.box(620, yy, 260, BOX_H, fw_txt, *COLOURS.get(fw_role, COLOURS["internal_fw"]))
                net = ipaddress.ip_network(a["prefix"])
                p.edge(left, right, 2, label=f"{a['name']} | VLAN {a['vlan']} | {net} ({hosts}) | eBGP")
            if not mine:
                p.text(20, y + 36, 600, 20, "No firewall attachment in this site (carried inside the fabric only)", 10)
            y += band_h + 20
        return p

    # ------------------------------------------------------------ Path: internet left, server right
    PATH_ORDER = ("isp_router", "edge", "internet_fw", "border_leaf", "service_leaf", "internal_fw", "leaf")

    def path(self, devices, cables, if_owner, if_name, if_addr, data) -> Page:
        p = Page("Path")
        p.text(0, 10, 1200, 30, f"Internet path: carrier > ISP router > edge > Internet FW > border leaf ({DESIGN['transit_vrf']}) > service leaf > Internal FW > server leaves", 14, True)
        att = self.attachments(data)
        ids, x = {}, 0
        carrier_box = p.box(x, 120, BOX_W, BOX_H, "Carriers\n(outside Infrahub)", "#ffffff", "#999999", extra="dashed=1;")
        x += BOX_W + 90
        for role in self.PATH_ORDER:
            names = sorted([n for n, d in devices.items() if d["role"] == role], key=natural)
            if role == "leaf":
                ids["leaf"] = p.box(x, 120, BOX_W + 20, BOX_H + 20, f"Server leaves\n{len(names)} devices\nEVPN, VRF gateways", *COLOURS["leaf"])
                break
            for k, n in enumerate(names[:2]):
                d = devices[n]
                extra = []
                if d["asn"]:
                    extra.append(f"AS {d['asn']}")
                for a in att:
                    if n in a["members"]:
                        net = ipaddress.ip_network(a["prefix"])
                        extra.append(f"{a['vrf']} {net[DESIGN['attachment_leaf_hosts'][a['members'].index(n)]]}")
                    elif any(n == f for f, _ in a["fws"]) and a["vrf"] == DESIGN["transit_vrf"]:
                        extra.append(f"{DESIGN['transit_vrf']} {ipaddress.ip_network(a['prefix'])[DESIGN['attachment_device_host']]}")
                ids[n] = p.box(x, 60 + k * 140, BOX_W + 40, BOX_H + 14 * len(extra), "\n".join([n] + extra), *COLOURS[role])
            x += BOX_W + 130
        for c in cables:                               # physical hops along the path, solid
            a, b = c["ends"]
            da, db = if_owner[a], if_owner[b]
            if da in ids and db in ids and devices[da]["role"] != devices[db]["role"]:
                p.edge(ids[da], ids[db], 1, src_label=if_name[a], dst_label=if_name[b])
        for n, d in devices.items():                   # carrier legs: upstream interfaces on the ISP routers
            if d["role"] == "isp_router" and n in ids:
                p.edge(carrier_box, ids[n], 1, dashed=True, label="carrier eBGP")
        for a in att:                                  # logical eBGP over each attachment (dashed)
            for m in a["members"]:
                for f, asn in a["fws"]:
                    if m in ids and f in ids and a["vrf"] == DESIGN["transit_vrf"]:
                        p.edge(ids[m], ids[f], 1, dashed=True, label=f"eBGP {a['vrf']} VLAN {a['vlan']}")
        sl = [n for n in ids if devices.get(n, {}).get("role") == "service_leaf"]
        if "leaf" in ids:
            for n in sl:
                p.edge(ids[n], ids["leaf"], 1, dashed=True, label="EVPN (tenant VRFs)")
        return p

    # ------------------------------------------------------------ Racks
    # ------------------------------------------------------------ OOB: OOB devices, their cables, home-run counts
    OOB_ROLES = ("oob_fw", "oob_agg", "oob_access", "console_server")

    def oob(self, devices, cables, if_owner, if_name) -> Page:
        p = Page("OOB")
        p.text(0, 10, 1400, 30, "Out-of-band: OOB firewalls, aggregation, access (network + BMC) and console servers; "
                                "management and console home runs counted per OOB device", 14, True)
        oob = {n: d for n, d in devices.items() if d["role"] in self.OOB_ROLES}
        mgmt, cons, links = {}, {}, []
        for c in cables:
            a, b = c["ends"]
            da, db = if_owner[a], if_owner[b]
            ia, ib = da in oob, db in oob
            if ia and ib:
                links.append((a, b, c["type"]))
            elif ia or ib:
                o = da if ia else db
                console = c["type"] == DESIGN["oob_cable_type_console"]
                (cons if console else mgmt)[o] = (cons if console else mgmt).get(o, 0) + 1
        rows = [[n for n in oob if oob[n]["role"] == "oob_fw"],
                [n for n in oob if oob[n]["role"] == "oob_agg"],
                [n for n in oob if oob[n]["role"] in ("console_server",) or (oob[n]["role"] == "oob_access" and "BMC" not in n)],
                [n for n in oob if oob[n]["role"] == "oob_access" and "BMC" in n]]
        ids, w, h = {}, 150, 62
        for r, names in enumerate(rows):
            names.sort(key=lambda n: (oob[n]["role"], natural(n)))
            x0 = max(0, (8 - len(names)) * (w + 30) // 2) if r < 3 else 0
            for k, n in enumerate(names):
                d = oob[n]
                extra = [f"{d['rack'] or '?'} U{d['u']}" if d["u"] else (d["rack"] or "no rack")]
                if mgmt.get(n):
                    extra.append(f"{mgmt[n]} mgmt home runs")
                if cons.get(n):
                    extra.append(f"{cons[n]} console home runs")
                ids[n] = p.box(x0 + k * (w + 30), 70 + r * 170, w, h, "\n".join([n, ROLE_LABEL[d["role"]]] + extra),
                               *COLOURS[d["role"]])
        for a, b, t in sorted(links, key=lambda l: (natural(if_owner[l[0]]), natural(if_name[l[0]]))):
            p.edge(ids[if_owner[a]], ids[if_owner[b]], 1, dashed=(t == DESIGN["oob_cable_type_console"]),
                   src_label=short_port(if_name[a]), dst_label=short_port(if_name[b]))
        p.text(0, 70 + 4 * 170, 900, 20, f"{len(links)} cables between OOB devices; {sum(mgmt.values())} management and "
                                         f"{sum(cons.values())} console home runs from the other devices of the site", 11)
        return p

    def racks(self, site, devices, data) -> Page:
        p = Page("Racks")
        pdu_u = "/".join(f"{h}U" for h in sorted({type_value(devices, n, "height", "height") for n, d in devices.items() if d["role"] == "pdu"}))
        pdu_note = f"{pdu_u}, {DESIGN['pdu_rack_face']}" if pdu_u else DESIGN["pdu_rack_face"]
        p.text(0, 10, 900, 30, f"{site} racks: front elevations; PDUs ({pdu_note}) listed under each rack", 14, True)
        racks = sorted((e["node"] for e in data["LocationRack"]["edges"]),
                       key=lambda r: (r["row"]["value"], r["row_position"]["value"]))
        res = {}
        for e in data["LocationRackReservation"]["edges"]:
            r = e["node"]
            rk = r["rack"]["node"]["name"]["value"]
            res.setdefault(rk, []).append(r)
        rows = sorted({r["row"]["value"] for r in racks})
        for r in racks:
            name, row, pos = r["name"]["value"], r["row"]["value"], r["row_position"]["value"]
            h = r["height"]["value"]
            x = (pos - 1) * (RACK_W + 30)
            y0 = 60 + rows.index(row) * (h * U_PX + 160)
            kw = r["power_budget_kw"]["value"]
            p.text(x, y0, RACK_W, 20, f"{name} ({r['purpose']['value']}{', ' + str(kw) + ' kW' if kw else ''})", 11, True)
            p.box(x, y0 + 24, RACK_W, h * U_PX, "", "#ffffff", "#999999", extra="rounded=0;")
            top = y0 + 24

            def slot(lo, height, text, fill, stroke):
                p.box(x + 4, top + (h - (lo + height - 1)) * U_PX, RACK_W - 8, height * U_PX,
                      f"U{lo}{'-' + str(lo + height - 1) if height > 1 else ''} {text}", fill, stroke, extra="rounded=0;fontSize=9;")
            for rv in res.get(name, []):
                slot(rv["position"]["value"], rv["height"]["value"], rv["label"]["value"], "#f5f5f5", "#999999")
            pdus = []
            for dn, d in devices.items():
                if d["rack"] != name:
                    continue
                if d["role"] == "pdu":
                    pdus.append(dn)
                elif d["u"]:
                    slot(d["u"], type_value(devices, dn, "height", "height"), dn, *COLOURS.get(d["role"], ("#ffffff", "#000000")))
            if pdus:
                p.text(x, top + h * U_PX + 4, RACK_W, 18, "PDUs: " + ", ".join(sorted(x.split("-")[-1] for x in pdus)), 9)
        return p
