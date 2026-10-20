"""site_cable_schedule: vendor cable schedule per site (P2-183), Markdown (P2-223).

Every cross-rack cable runs: device -> jumper -> rack-local panel -> trunk -> patch rack block -> cross-connect jumper
-> patch rack block -> trunk -> rack-local panel -> jumper -> device. Same-rack cables are direct DAC (P2-174).
Rules: blocks per rack (P2-214, P2-217, P2-218), panel names <rack>-PP<U> (P2-219), patch rack layout (P2-220),
same port number at both ends of a trunk (P2-221), label order (P2-222), lengths (P2-224, P2-225), BOM (P2-226),
rack-local panel U42 (P2-145, P2-227). Patch rack per cable: nearest of B05-B07 by tray distance (P2-182), ties to B06 (P2-229).
"""
import re
from collections import Counter

from infrahub_sdk.transforms import InfrahubTransform

RACK_WIDTH_M, ROW_PITCH_M, VERTICAL_PER_END_M = 0.8, 3.0, 3.0      # P2-178, P2-179, P2-180
ROW_INDEX = {"A": 0, "B": 1, "C": 2, "D": 3}
U_HEIGHT_M, SLACK_M = 0.04445, 1.0                                    # P2-196, P2-224
STOCK_M = (1, 2, 3, 5, 7, 10, 15, 20, 30)                             # P2-181
PATCH_RACKS = ("B05", "B06", "B07")                                   # P2-182
TIE_BREAK = "B06"                                                     # P2-229: centre rack, no carrier
LOCAL_PANEL_U = 42                                                    # P2-145, P2-227
SERVER_BLOCK, NET_BLOCK = 12, 144                                     # P2-217, P2-218
PATCH_PANEL_U = {"net1": 42, "net2": 41, "A": 40, "C": 39}            # P2-220
SHORT = (("Ethernet", "Eth"), ("FourHundredGigE", "FH"))


def short(n):
    for a, b in SHORT:
        if n.startswith(a):
            return b + n[len(a):]
    return n


def natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def stock(m, what):
    for s in STOCK_M:
        if m <= s:
            return s
    raise ValueError(f"{what}: {m:.1f} m exceeds {STOCK_M[-1]} m")


def jumper(u1, u2, what):
    return stock(abs(u1 - u2) * U_HEIGHT_M + SLACK_M, what)


class SiteCableSchedule(InfrahubTransform):
    query = "site_cable_schedule"

    async def transform(self, data: dict) -> str:
        racks = {}
        for e in data["LocationRack"]["edges"]:
            r = e["node"]
            racks[r["name"]["value"]] = {"row": r["row"]["value"], "pos": r["row_position"]["value"], "purpose": r["purpose"]["value"]}
        site = next(iter(racks)).split("-")[0]
        net = sorted(n for n, r in racks.items() if r["purpose"] == "network")          # NET-A (B04) then NET-B (B08)
        if len(net) != 2:
            raise ValueError(f"expected 2 network racks, found {net}")

        def xy(n):
            return (racks[n]["pos"] - 1) * RACK_WIDTH_M, ROW_INDEX[racks[n]["row"]] * ROW_PITCH_M

        def tray(a, b):
            (ax, ay), (bx, by) = xy(a), xy(b)
            return abs(ax - bx) + abs(ay - by)

        def block(rack):
            """(patch panel U, first port, block size) of this rack's block in every patch rack (P2-220)."""
            r = racks[rack]
            if rack in net:
                return PATCH_PANEL_U["net1" if rack == net[0] else "net2"], 1, NET_BLOCK
            if r["purpose"] == "server" and r["row"] in ("A", "C"):
                return PATCH_PANEL_U[r["row"]], (r["pos"] - 1) * SERVER_BLOCK + 1, SERVER_BLOCK
            raise ValueError(f"{rack}: purpose {r['purpose']} has no patch block (P2-220)")

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
            if c["length_m"]["value"] is None:                                    # fabric cables only (P2-237)
                continue
            label = c["name"]["value"]
            a, b = ends if label.startswith(ends[0][0] + ":") else ends[::-1]       # upstream end first, as in the label (P2-171)
            cables.append({"label": label, "type": c["cable_type"]["value"], "length": c["length_m"]["value"], "a": a, "b": b})
        cables.sort(key=lambda c: natural(c["label"]))                               # P2-222

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
