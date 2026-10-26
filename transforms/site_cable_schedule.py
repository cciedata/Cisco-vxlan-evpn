"""site_cable_schedule: vendor cable schedule per site, Markdown.

Every cross-rack cable runs: device -> jumper -> rack-local panel -> trunk -> patch rack block -> cross-connect jumper
-> patch rack block -> trunk -> rack-local panel -> jumper -> device. Same-rack cables are direct DAC.
Rules: blocks per rack, panel names <rack>-PP<U>, patch rack layout,
same port number at both ends of a trunk, label order, lengths, BOM,
rack-local panel at rack_local_panel_u. Patch rack per cable: nearest of network_patch_racks by tray distance,
ties to patch_rack_tie_break.
"""
import re
from collections import Counter

from infrahub_sdk.transforms import InfrahubTransform

U_HEIGHT_M = 0.04445                                                  # EIA-310 rack unit (physical constant)
SHORT = (("Ethernet", "Eth"), ("FourHundredGigE", "FH"))
# Physical / cabling design values: read from FabricDesign at the start of transform() (configure)
DECIMAL_FIELDS = ("rack_width_m", "row_pitch_m", "vertical_per_end_m", "cable_slack_m")
DESIGN_FIELDS = ("rack_width_m", "row_pitch_m", "vertical_per_end_m", "row_index", "cable_slack_m", "cable_stock_lengths_m",
                 "network_patch_racks", "patch_rack_tie_break", "rack_local_panel_u", "patch_block_ports_network_rack",
                 "patch_block_ports_server_rack", "patch_panel_u", "network_racks", "server_rows", "patch_block_first_port")
DESIGN: dict = {}


def configure(data: dict) -> None:
    fd = data.get("FabricDesign") or {}
    edges = fd.get("edges") or []
    if fd.get("count") != 1 or len(edges) != 1:
        raise ValueError(f"FabricDesign: expected exactly 1 object, found {fd.get('count', len(edges))}; fix it in Infrahub "
                         "(and check that the query asks for FabricDesign)")
    node = edges[0]["node"]
    DESIGN.clear()
    for f in DESIGN_FIELDS:
        value = (node.get(f) or {}).get("value")
        if value is None:
            raise ValueError(f"FabricDesign.{f} is empty; fix it in Infrahub")
        DESIGN[f] = float(value) if f in DECIMAL_FIELDS else value  # decimals stored as Text


def short(n):
    for a, b in SHORT:
        if n.startswith(a):
            return b + n[len(a):]
    return n


def natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def stock(m, what):
    for s in DESIGN["cable_stock_lengths_m"]:
        if m <= s:
            return s
    raise ValueError(f"{what}: {m:.1f} m exceeds {DESIGN['cable_stock_lengths_m'][-1]} m")


def jumper(u1, u2, what):
    return stock(abs(u1 - u2) * U_HEIGHT_M + DESIGN["cable_slack_m"], what)


class SiteCableSchedule(InfrahubTransform):
    query = "site_cable_schedule"

    async def transform(self, data: dict) -> str:
        configure(data)
        RACK_WIDTH_M, ROW_PITCH_M, VERTICAL_PER_END_M = DESIGN["rack_width_m"], DESIGN["row_pitch_m"], DESIGN["vertical_per_end_m"]
        ROW_INDEX, PATCH_PANEL_U = DESIGN["row_index"], DESIGN["patch_panel_u"]
        PATCH_RACKS, TIE_BREAK, LOCAL_PANEL_U = DESIGN["network_patch_racks"], DESIGN["patch_rack_tie_break"], DESIGN["rack_local_panel_u"]
        SERVER_BLOCK, NET_BLOCK = DESIGN["patch_block_ports_server_rack"], DESIGN["patch_block_ports_network_rack"]
        SERVER_ROWS, FIRST_PORT = tuple(DESIGN["server_rows"]), DESIGN["patch_block_first_port"]
        racks = {}
        for e in data["LocationRack"]["edges"]:
            r = e["node"]
            racks[r["name"]["value"]] = {"row": r["row"]["value"], "pos": r["row_position"]["value"], "purpose": r["purpose"]["value"]}
        site = next(iter(racks)).split("-")[0]
        # network rack 1, 2 (net1 / net2 patch panels) from FabricDesign.network_racks, as generators/site_physical.py
        net = [f"{site}-{DESIGN['network_racks'][k]}" for k in sorted(DESIGN["network_racks"], key=int)]
        found = sorted(n for n, r in racks.items() if r["purpose"] == "network")
        if sorted(net) != found:
            raise ValueError(f"FabricDesign.network_racks gives {net}, the site's network racks are {found}")

        def xy(n):
            return (racks[n]["pos"] - 1) * RACK_WIDTH_M, ROW_INDEX[racks[n]["row"]] * ROW_PITCH_M

        def tray(a, b):
            (ax, ay), (bx, by) = xy(a), xy(b)
            return abs(ax - bx) + abs(ay - by)

        def block(rack):
            """(patch panel U, first port, block size) of this rack's block in every patch rack."""
            r = racks[rack]
            if rack in net:
                return PATCH_PANEL_U["net1" if rack == net[0] else "net2"], FIRST_PORT, NET_BLOCK
            if r["purpose"] == "server" and r["row"] in SERVER_ROWS:
                return PATCH_PANEL_U[r["row"]], FIRST_PORT + (r["pos"] - 1) * SERVER_BLOCK, SERVER_BLOCK
            raise ValueError(f"{rack}: purpose {r['purpose']} has no patch block")

        owner = {}
        for e in data["DcimDevice"]["edges"]:
            d = e["node"]
            rack = (((d.get("rack") or {}).get("node") or {}).get("name") or {}).get("value")
            for ie in d["interfaces"]["edges"]:
                owner[ie["node"]["id"]] = (d["name"]["value"], ie["node"]["name"]["value"], rack, (d.get("position") or {}).get("value"))

        cables = []
        for e in data["DcimCable"]["edges"]:
            c = e["node"]
            ends = [owner.get(x["node"]["id"]) for x in c["connected_endpoints"]["edges"]]
            if len(ends) != 2 or None in ends:
                continue
            if c["length_m"]["value"] is None:                                    # fabric cables only
                continue
            label = c["name"]["value"]
            a, b = ends if label.startswith(ends[0][0] + ":") else ends[::-1]       # upstream end first, as in the label
            cables.append({"label": label, "type": c["cable_type"]["value"], "length": c["length_m"]["value"], "a": a, "b": b})
        cables.sort(key=lambda c: natural(c["label"]))

        local_index = {}
        for c in cables:
            for end in (c["a"], c["b"]):
                if c["a"][2] != c["b"][2]:
                    local_index.setdefault(end[2], []).append(c["label"])

        def port_no(rack, label):
            n = local_index[rack].index(label) + 1
            u, first, size = block(rack)
            if n > size:
                raise ValueError(f"{rack}: {n} cross-rack cables exceed its {size}-port block")
            return n, u, first + n - 1

        rows, direct, bom = [], [], Counter()
        for c in cables:
            a, b = c["a"], c["b"]
            if a[2] == b[2]:
                direct.append(f"| {c['label']} | {c['type']} | {c['length']} | {a[2]} |")
                bom[(f"{c['type']} direct", c["length"])] += 1
                continue
            via = min(PATCH_RACKS, key=lambda p: (round(tray(a[2], f"{site}-{p}") + tray(f"{site}-{p}", b[2]), 3), p != TIE_BREAK))
            vr = f"{site}-{via}"
            na, ua, pa = port_no(a[2], c["label"])
            nb, ub, pb = port_no(b[2], c["label"])
            ja = jumper(a[3], LOCAL_PANEL_U, c["label"])
            jb = jumper(b[3], LOCAL_PANEL_U, c["label"])
            ta = stock(tray(a[2], vr) + 2 * VERTICAL_PER_END_M, c["label"])
            tb = stock(tray(vr, b[2]) + 2 * VERTICAL_PER_END_M, c["label"])
            xc = jumper(ua, ub, c["label"])
            for part, m in (("jumper", ja), ("trunk leg", ta), ("cross-connect jumper", xc), ("trunk leg", tb), ("jumper", jb)):
                bom[(f"{c['type']} {part}", m)] += 1
            rows.append(f"| {c['label']} | {c['type']} | {a[0]}:{short(a[1])} | {ja} | {a[2]}-PP{LOCAL_PANEL_U}:{na} | {ta} | "
                        f"{vr}-PP{ua}:{pa} | {xc} | {vr}-PP{ub}:{pb} | {tb} | {b[2]}-PP{LOCAL_PANEL_U}:{nb} | {jb} | {b[0]}:{short(b[1])} |")

        out = [f"# {site}: vendor cable schedule", "",
               "Generated by Infrahub (site_cable_schedule transform). Do not edit by hand. Lengths in metres.", "",
               f"## Cross-rack cables via patch racks ({len(rows)})", "",
               "| Cable label | Media | A end | A jumper | A panel:port | Trunk A | Patch in | X-connect | Patch out | Trunk B | B panel:port | B jumper | B end |",
               "|---|---|---|---|---|---|---|---|---|---|---|---|---|", *rows, "",
               f"## Same-rack direct cables ({len(direct)})", "", "| Cable label | Media | Length | Rack |", "|---|---|---|---|", *direct, "",
               "## Bill of materials", "", "| Part | Length (m) | Quantity |", "|---|---|---|"]
        out += [f"| {p} | {m} | {q} |" for (p, m), q in sorted(bom.items())]
        used = {r: len(v) for r, v in sorted(local_index.items(), key=lambda t: natural(t[0]))}
        out += ["", "## Panel ports used per rack", "", "| Rack | Ports used | Block size |", "|---|---|---|"]
        out += [f"| {r} | {n} | {block(r)[2]} |" for r, n in used.items()]
        return "\n".join(out) + "\n"
