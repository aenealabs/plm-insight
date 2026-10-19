"""Generate the fictional PLM export used by the offline demo.

Deterministic (seeded), so the dataset, the planted problems, and the detection numbers are
the same on every machine. Writes data/demo/parts.csv, bom.csv, and planted.json (what was
planted where, used to measure how well the rules find it). Nothing here is real company
data; part numbers, descriptions, and people are invented.

Usage: python scripts/generate_dataset.py
"""

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "demo"
rng = random.Random(20261018)
EXPORT_DATE = date(2026, 10, 1)  # no part is created after the export was taken

BUSINESS_UNITS = {  # name: export-controlled business?
    "Electrical Products": False,
    "Aerospace Systems": True,
    "eMobility": False,
    "Industrial Controls": False,
}
ENGINEERS = ["A. Novak", "B. Osei", "C. Lindqvist", "D. Mehta", "E. Duarte", "F. Haddad",
             "G. Brennan", "H. Sato", "I. Moreau", "J. Adeyemi", "K. Petrov", "L. Fischer"]
CLASSES_CONTROLLED = ["9A991", "9A610.x", "3A991", "EAR99"]

# part type: (make/buy, materials, size generator, canonical description builder, variants)
FASTENER_MAT = ["ZINC PLATED STEEL", "STAINLESS STEEL 316", "STAINLESS STEEL 304", "BRASS"]
SHEET_MAT = ["ALUMINUM 6061-T6", "STEEL CRS 1008", "STAINLESS STEEL 304"]
PLASTIC_MAT = ["PC/ABS UL94 V-0", "PA66 GF30", "PBT GF30"]
COPPER_MAT = ["COPPER C110", "TINNED COPPER"]

MAT_ABBR = {"ZINC PLATED STEEL": "ZN STL", "STAINLESS STEEL 316": "SS316",
            "STAINLESS STEEL 304": "SS304", "BRASS": "BRS", "ALUMINUM 6061-T6": "AL6061",
            "STEEL CRS 1008": "CRS", "PC/ABS UL94 V-0": "PC-ABS", "PA66 GF30": "PA66-GF30",
            "PBT GF30": "PBT-GF30", "COPPER C110": "CU C110", "TINNED COPPER": "SN CU"}


def fastener():
    kind = rng.choice(["HEX BOLT", "SOCKET HEAD CAP SCREW", "PAN HEAD SCREW", "HEX NUT",
                       "FLAT WASHER"])
    thread = rng.choice(["M3", "M4", "M5", "M6", "M8", "M10", "M12"])
    length = rng.choice([6, 8, 10, 12, 16, 20, 25, 30, 35, 40, 50])
    size = thread if kind in ("HEX NUT", "FLAT WASHER") else f"{thread}X{length}"
    return "Fastener", "buy", rng.choice(FASTENER_MAT), size, kind


def bracket():
    kind = rng.choice(["MOUNTING BRACKET", "L BRACKET", "SUPPORT PLATE", "COVER PLATE"])
    size = f"{rng.randrange(40, 400, 5)}X{rng.randrange(20, 200, 5)}X{rng.choice([1.5, 2, 3, 4])}"
    return "Sheet metal", "make", rng.choice(SHEET_MAT), size, kind


def molded():
    kind = rng.choice(["ENCLOSURE BASE", "ENCLOSURE COVER", "TERMINAL COVER", "CABLE GLAND"])
    size = f"{rng.randrange(30, 300, 5)}X{rng.randrange(30, 300, 5)}X{rng.randrange(10, 120, 5)}"
    return "Molded part", "buy", rng.choice(PLASTIC_MAT), size, kind


def busbar():
    kind = rng.choice(["BUSBAR", "FLEXIBLE BUSBAR", "GROUND STRAP"])
    size = f"{rng.randrange(50, 600, 10)}X{rng.choice([10, 12, 15, 20, 25, 30, 40])}X" \
           f"{rng.choice([2, 3, 4, 5, 6, 8, 10])}"
    return "Conductor", "make", rng.choice(COPPER_MAT), size, kind


WEIGHT_KG = {"Fastener": (0.001, 0.08), "Sheet metal": (0.05, 1.6), "Molded part": (0.02, 0.9),
             "Conductor": (0.05, 3.2)}
GENERATORS = [(fastener, 0.40), (bracket, 0.22), (molded, 0.18), (busbar, 0.20)]


def describe(kind: str, size: str, material: str, style: int) -> str:
    """Three ways engineers write the same thing (the cause of duplicates)."""
    mat = MAT_ABBR[material]
    if style == 0:
        return f"{kind} {size} {mat}"
    if style == 1:
        words = kind.split()
        lead = f"{words[-1]}, {' '.join(words[:-1])}" if len(words) > 1 else words[0]
        return f"{lead.title()}, {size.lower().replace('x', ' x ')}, {material.title()}"
    return f"{kind.capitalize()} {size.lower()} {material.lower()}"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    parts: list[dict] = []
    specs: dict[str, tuple] = {}  # part number -> (spec, description style), not exported
    seen_keys: set[tuple] = set()
    next_pn = iter(range(4100, 99999))

    def pn() -> str:
        return f"{rng.choice([100, 210, 330, 450])}-{next(next_pn):06d}"

    def new_part(bu: str, spec=None, style=None, lifecycle=None, created=None) -> dict:
        while spec is None:
            gen = rng.choices([g for g, _ in GENERATORS], [w for _, w in GENERATORS])[0]
            candidate = gen()
            key = (candidate[0], candidate[2], candidate[3], candidate[4])
            if key not in seen_keys:  # non-planted parts are genuinely distinct
                seen_keys.add(key)
                spec = candidate
        ptype, source, material, size, kind = spec
        controlled = BUSINESS_UNITS[bu]
        style = rng.randrange(3) if style is None else style
        p = {"part_number": pn(), "description": describe(kind, size, material, style),
             "part_type": ptype, "business_unit": bu, "material": material, "size": size,
             "uom": "EA", "weight_kg": f"{rng.uniform(*WEIGHT_KG[ptype]):.3f}",
             "make_buy": source,
             "lifecycle": lifecycle or rng.choices(["Released", "In Work", "Obsolete"],
                                                   [0.86, 0.08, 0.06])[0],
             "export_class": rng.choice(CLASSES_CONTROLLED) if controlled else
             rng.choice(["EAR99", "EAR99", "EAR99", ""]),
             "superseded_by": "",
             "created": (created or date(2012, 1, 1) + timedelta(days=rng.randrange(5000)))
             .isoformat(),
             "created_by": rng.choice(ENGINEERS)}
        if p["export_class"] == "" and not controlled:
            p["export_class"] = "EAR99"
        specs[p["part_number"]] = (spec, style)
        parts.append(p)
        return p

    bus = list(BUSINESS_UNITS)
    for _ in range(4400):
        new_part(rng.choices(bus, [0.35, 0.2, 0.25, 0.2])[0])

    planted: dict[str, list] = {k: [] for k in
                                ("DQ-DUP", "DQ-EXP", "DQ-REL", "DQ-ORPHAN", "DQ-OBS",
                                 "DQ-ATTR", "DQ-UOM")}

    # duplicates: the same spec created again, often in another business unit, worded differently
    originals = [p for p in parts if p["lifecycle"] == "Released"]
    for orig in rng.sample(originals, 130):
        spec, orig_style = specs[orig["part_number"]]
        cluster = [orig["part_number"]]
        for _ in range(rng.choice([1, 1, 1, 2])):
            other_style = rng.choice([s for s in range(3) if s != orig_style])
            dup = new_part(rng.choice(bus), spec=spec, style=other_style, lifecycle="Released",
                           created=min(date.fromisoformat(orig["created"])
                                       + timedelta(days=rng.randrange(60, 2500)), EXPORT_DATE))
            # the same physical part weighs the same, give or take how it was measured
            dup["weight_kg"] = f"{float(orig['weight_kg']) * rng.uniform(0.97, 1.03):.3f}"
            cluster.append(dup["part_number"])
        planted["DQ-DUP"].append(sorted(cluster))

    # missing required attributes
    for p in rng.sample([p for p in parts if p["make_buy"] == "buy"], 90):
        p["weight_kg"] = ""
        planted["DQ-ATTR"].append(p["part_number"])
    for p in rng.sample([p for p in parts if p["material"]], 60):
        if p["part_number"] not in planted["DQ-ATTR"]:
            p["material"] = ""
            planted["DQ-ATTR"].append(p["part_number"])

    # successors for some obsolete parts
    obsolete = [p for p in parts if p["lifecycle"] == "Obsolete"]
    released = [p for p in parts if p["lifecycle"] == "Released"]
    for p in obsolete[: len(obsolete) // 2]:
        same = [r for r in released if r["part_type"] == p["part_type"]]
        p["superseded_by"] = rng.choice(same)["part_number"]

    # assemblies and BOMs
    bom: list[dict] = []
    assemblies = []
    for _ in range(520):
        bu = rng.choices(bus, [0.35, 0.2, 0.25, 0.2])[0]
        product = rng.choice(["PANEL", "DRIVE", "BREAKER", "CONTACTOR", "CHARGER", "ACTUATOR",
                              "POWER UNIT", "CONTROLLER"])
        asm = {"part_number": pn(),
               "description": f"{product} ASSY {rng.choice('ABCD')}{rng.randrange(10, 99)}",
               "part_type": "Assembly", "business_unit": bu, "material": "", "size": "",
               "uom": "EA", "weight_kg": f"{rng.uniform(0.5, 40):.2f}", "make_buy": "make",
               "lifecycle": rng.choices(["Released", "In Work"], [0.85, 0.15])[0],
               "export_class": rng.choice(CLASSES_CONTROLLED) if BUSINESS_UNITS[bu] else "EAR99",
               "superseded_by": "",
               "created": (date(2014, 1, 1) + timedelta(days=rng.randrange(4300))).isoformat(),
               "created_by": rng.choice(ENGINEERS)}
        parts.append(asm)
        assemblies.append(asm)
        pool = [p for p in released if p["part_type"] != "Assembly"]
        for find_no, child in enumerate(rng.sample(pool, rng.randrange(12, 34)), start=1):
            bom.append({"parent": asm["part_number"], "find_number": str(find_no * 10),
                        "child": child["part_number"],
                        "quantity": str(rng.choice([1, 1, 2, 2, 4, 6, 8])), "uom": child["uom"]})

    by_pn = {p["part_number"]: p for p in parts}
    released_asm = [a for a in assemblies if a["lifecycle"] == "Released"]

    # released assembly with an in-work child
    in_work = [p for p in parts if p["lifecycle"] == "In Work" and p["part_type"] != "Assembly"]
    for asm in rng.sample(released_asm, 25):
        child = rng.choice(in_work)
        bom.append({"parent": asm["part_number"], "find_number": "900",
                    "child": child["part_number"], "quantity": "1", "uom": "EA"})
        planted["DQ-REL"].append([asm["part_number"], child["part_number"]])

    # obsolete part used in a released BOM
    for asm in rng.sample(released_asm, 30):
        child = rng.choice(obsolete)
        bom.append({"parent": asm["part_number"], "find_number": "910",
                    "child": child["part_number"], "quantity": "2", "uom": "EA"})
        planted["DQ-OBS"].append([asm["part_number"], child["part_number"]])

    # orphan BOM lines (child does not exist: typo or deleted part)
    for asm in rng.sample(assemblies, 15):
        real = rng.choice([b for b in bom if b["parent"] == asm["part_number"]])["child"]
        ghost = real[:-2] + f"{(int(real[-2:]) + 37) % 100:02d}"
        while ghost in by_pn:
            ghost = ghost[:-2] + f"{(int(ghost[-2:]) + 1) % 100:02d}"
        bom.append({"parent": asm["part_number"], "find_number": "920", "child": ghost,
                    "quantity": "1", "uom": "EA"})
        planted["DQ-ORPHAN"].append([asm["part_number"], ghost])

    # conflicting units of measure across BOM lines
    multi_use: dict[str, list[dict]] = {}
    for line in bom:
        multi_use.setdefault(line["child"], []).append(line)
    candidates = [c for c, lines in multi_use.items() if len(lines) >= 3 and c in by_pn
                  and by_pn[c]["part_type"] in ("Conductor", "Fastener")]
    for child in rng.sample(candidates, 12):
        line = rng.choice(multi_use[child])
        line["uom"] = "MM" if by_pn[child]["part_type"] == "Conductor" else "PK"
        planted["DQ-UOM"].append(child)

    # missing export classification: parts in the controlled business, and parts used under
    # controlled assemblies
    controlled = [p for p in parts if BUSINESS_UNITS[p["business_unit"]]]
    for p in rng.sample(controlled, 160):
        p["export_class"] = ""
        planted["DQ-EXP"].append(p["part_number"])
    controlled_asm = {a["part_number"] for a in assemblies if BUSINESS_UNITS[a["business_unit"]]}
    used_under = sorted({b["child"] for b in bom if b["parent"] in controlled_asm
                         and b["child"] in by_pn
                         and not BUSINESS_UNITS[by_pn[b["child"]]["business_unit"]]})
    for pn_ in rng.sample(used_under, 40):
        by_pn[pn_]["export_class"] = ""
        planted["DQ-EXP"].append(pn_)

    fields = ["part_number", "description", "part_type", "business_unit", "material", "size",
              "uom", "weight_kg", "make_buy", "lifecycle", "export_class", "superseded_by",
              "created", "created_by"]
    with (OUT / "parts.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(sorted(parts, key=lambda p: p["part_number"]))
    with (OUT / "bom.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["parent", "find_number", "child", "quantity", "uom"])
        w.writeheader()
        w.writerows(bom)
    (OUT / "planted.json").write_text(json.dumps(
        {"_note": "Problems planted by scripts/generate_dataset.py, used to measure detection.",
         **planted}, indent=1) + "\n")
    print(f"{len(parts)} parts, {len(bom)} BOM lines; planted: "
          + ", ".join(f"{k} {len(v)}" for k, v in planted.items()))


if __name__ == "__main__":
    main()
