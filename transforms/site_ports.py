"""site_ports: one .drawio file per site, one tab per switch, every front-panel port (P2-106, P2-108).

Port counts come from the device type (design-decisions.md, platforms). Faceplate layout (P2-216): odd ports top row, even ports bottom row. Uncabled ports read "<port> unused" (P2-215).
Colour = interface role; label = far end device:port from the cable (P2-151), with cable type and length.
"""
from html import escape

from infrahub_sdk.transforms import InfrahubTransform

SWITCH_ROLES = ("spine", "leaf", "border_leaf", "service_leaf", "bgw", "edge", "dci", "dmz")
FRONT_PANEL = {"N9K-C9364D-GX2A": 64, "N9K-C9332D-GX2B": 34, "N9K-C93180YC-FX3": 54}   # 64x400G; 32x400G+2x10G; 48x25G+6x100G
ROLE_COLOUR = {            # draw.io built-in pairs (P2-200)
    "core": ("#dae8fc", "#6c8ebf"), "vpc_peer_link": ("#e1d5e7", "#9673a6"), "vpc_keepalive": ("#fff2cc", "#d6b656"),
    "peering": ("#f8cecc", "#b85450"), "upstream": ("#f8cecc", "#b85450"), "management": ("#f5f5f5", "#666666"),
}
UNUSED = ("#ffffff", "#bbbbbb")
SHORT = (("Ethernet", "Eth"), ("FourHundredGigE", "FH"))
PORT_W, PORT_H = 150, 60


def short(n):
    for a, b in SHORT:
        if n.startswith(a):
            return b + n[len(a):]
    return n


def natural(name):
    import re
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


class SitePorts(InfrahubTransform):
    query = "site_ports"

    async def transform(self, data: dict) -> str:
        owner, ifname, far = {}, {}, {}
        devices = {}
        for e in data["DcimDevice"]["edges"]:
            d = e["node"]
            name = d["name"]["value"]
            devices[name] = {"role": d["role"]["value"],
                             "type": (((d.get("device_type") or {}).get("node") or {}).get("name") or {}).get("value"), "ifs": {}}
            for ie in d["interfaces"]["edges"]:
                i = ie["node"]
                owner[i["id"]], ifname[i["id"]] = name, i["name"]["value"]
                devices[name]["ifs"][i["name"]["value"]] = {"id": i["id"], "role": (i.get("role") or {}).get("value"),
                                                           "desc": (i.get("description") or {}).get("value") or ""}
        for e in data["DcimCable"]["edges"]:
            c = e["node"]
            ends = [x["node"]["id"] for x in c["connected_endpoints"]["edges"]]
            if len(ends) == 2 and all(x in owner for x in ends):
                for a, b in (ends, ends[::-1]):
                    far[a] = f"{owner[b]}:{short(ifname[b])} ({c['cable_type']['value']}" + (f", {c['length_m']['value']} m)" if c['length_m']['value'] is not None else ")")
        pages = []
        for name in sorted((n for n, d in devices.items() if d["role"] in SWITCH_ROLES), key=natural):
            pages.append(self.page(name, devices[name], far))
        return '<mxfile host="infrahub">' + "".join(pages) + "</mxfile>"

    def page(self, name, d, far) -> str:
        cells, n = [], [2]

        def cell(x, y, w, h, text, fill, stroke, extra=""):
            n[0] += 1
            cells.append(f'<mxCell id="p{n[0]}" value="{escape(text)}" style="rounded=0;whiteSpace=wrap;html=1;fontSize=9;'
                         f'fillColor={fill};strokeColor={stroke};{extra}" vertex="1" parent="1">'
                         f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        count = FRONT_PANEL.get(d["type"], 0)
        names = [f"Ethernet1/{k}" for k in range(1, count + 1)]
        extra_ifs = sorted((i for i in d["ifs"] if i not in names and not i.startswith(("loopback", "Vlan", "port-channel", "nve"))), key=natural)
        cols = (count + 1) // 2 + len(extra_ifs)
        cell(0, 0, max(cols, 8) * PORT_W, 30, f"{name} | {d['type']} | {count} front-panel ports | odd top, even bottom | colour = interface role",
             "#ffffff", "#ffffff", "fontSize=13;fontStyle=1;align=left;")

        def port_cell(x, y, port):
            i = d["ifs"].get(port)
            if i and i["id"] in far:
                fill, stroke = ROLE_COLOUR.get(i["role"], UNUSED)
                lines = [short(port), far[i["id"]]]
            elif i and i["role"] in ROLE_COLOUR and i["desc"]:
                fill, stroke = ROLE_COLOUR[i["role"]]
                lines = [short(port), i["desc"]]
            else:
                fill, stroke = UNUSED
                lines = [f"{short(port)} unused"]
            cell(x, y, PORT_W - 6, PORT_H, "<br>".join(lines), fill, stroke)

        for k, port in enumerate(names):
            port_cell((k // 2) * PORT_W, 40 + (k % 2) * (PORT_H + 6), port)
        for k, port in enumerate(extra_ifs):
            port_cell(((count + 1) // 2 + k) * PORT_W + 20, 40, port)
        y = 40 + 2 * (PORT_H + 6) + 20
        for k, (role, (fill, stroke)) in enumerate(list(ROLE_COLOUR.items()) + [("unused", UNUSED)]):
            cell(k * 130, y, 120, 22, role, fill, stroke)
        return (f'<diagram name="{escape(name)}" id="{escape(name.lower())}"><mxGraphModel grid="1" gridSize="10" page="0">'
                f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{"".join(cells)}</root></mxGraphModel></diagram>')
