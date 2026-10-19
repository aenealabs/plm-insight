"""Data quality rules over a PLM part master and BOM export. Pure functions, no AI.

Each rule has an id, a severity, and plain-language text shown next to every finding, and
each finding carries its evidence and a proposed fix. Export classification is never
proposed: a missing classification goes to Trade Compliance for review.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field

RULES = {
    "DQ-DUP": ("high", "Likely duplicate parts: same type and size, same material (or one "
                       "missing), and the same kind of part once abbreviations, case, and word "
                       "order are normalized"),
    "DQ-EXP": ("high", "Missing export classification on a part in an export-controlled "
                       "business unit, or used directly in a controlled assembly"),
    "DQ-REL": ("high", "Released assembly with an In Work child part"),
    "DQ-OBS": ("medium", "Obsolete part used in a released assembly"),
    "DQ-ORPHAN": ("high", "BOM line whose child part does not exist in the part master"),
    "DQ-UOM": ("medium", "BOM lines use a different unit of measure than the part"),
    "DQ-ATTR": ("low", "Required attribute missing: material for every component, weight for "
                       "purchased parts"),
}
RULE_NAMES = {"DQ-DUP": "Duplicate parts", "DQ-EXP": "Missing export classification",
              "DQ-REL": "Released with In Work child", "DQ-OBS": "Obsolete part in use",
              "DQ-ORPHAN": "Missing child part", "DQ-UOM": "Unit of measure conflict",
              "DQ-ATTR": "Missing attribute"}

# Common engineering abbreviations, applied to whole words after upper-casing.
ABBREVIATIONS = {
    "ZN": "ZINC PLATED", "STL": "STEEL", "SS316": "STAINLESS STEEL 316",
    "SS304": "STAINLESS STEEL 304", "BRS": "BRASS", "AL6061": "ALUMINUM 6061-T6",
    "CRS": "STEEL CRS 1008", "PC-ABS": "PC/ABS UL94 V-0", "PA66-GF30": "PA66 GF30",
    "PBT-GF30": "PBT GF30", "CU": "COPPER", "SN": "TINNED", "C110": "C110",
    "SHCS": "SOCKET HEAD CAP SCREW", "BRKT": "BRACKET", "ASSY": "ASSEMBLY",
}
STOP = {"PLATED"}


@dataclass
class Finding:
    rule: str
    part_number: str  # the part the finding is about (survivor for duplicates)
    business_unit: str
    title: str
    parts: list[str]  # every part involved
    evidence: dict = field(default_factory=dict)
    fix: dict = field(default_factory=dict)  # proposed change; empty for compliance review

    @property
    def severity(self) -> str:
        return RULES[self.rule][0]


def normalize(description: str) -> frozenset[str]:
    """Description as a set of canonical words: case, punctuation, abbreviations, word order,
    and 'm8 x 20' vs 'M8X20' no longer matter."""
    text = description.upper().replace(",", " ")
    text = re.sub(r"(\d)\s*X\s*(?=[\dM])", r"\1X", text)  # 20 x 50 -> 20X50
    words = []
    for w in text.split():
        words += ABBREVIATIONS.get(w, w).split()
    return frozenset(w for w in words if w not in STOP)


def jaccard(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b) if a or b else 0.0


def find_duplicates(parts: list[dict], where_used: dict[str, list[str]]) -> list[Finding]:
    blocks: dict[tuple, list[dict]] = defaultdict(list)
    for p in parts:
        if p["part_type"] != "Assembly" and p["size"]:
            blocks[(p["part_type"], p["size"].upper())].append(p)
    known = {p["material"] for p in parts if p["material"]}
    findings = []
    for (ptype, size), members in blocks.items():
        if len(members) < 2:
            continue
        # Size already matches within the block, and material is compared as an attribute,
        # so compare only the words that say what the part is ("hex bolt" vs "bolt, hex";
        # "hex nut" is not "flat washer").
        material = {p["part_number"]: p["material"] or material_from(p["description"], known)
                    for p in members}
        materials = normalize(" ".join(m for m in material.values() if m))
        words = {p["part_number"]: normalize(p["description"]) - normalize(size) - materials
                 for p in members}
        parent = {p["part_number"]: p["part_number"] for p in members}
        scores = {}
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                ma, mb = material[a["part_number"]], material[b["part_number"]]
                if ma and mb and ma != mb:
                    continue  # a missing material can still match; a different one cannot
                s = jaccard(words[a["part_number"]], words[b["part_number"]])
                if s >= 0.6:
                    parent[_root(parent, a["part_number"])] = _root(parent, b["part_number"])
                    scores[(a["part_number"], b["part_number"])] = s
        clusters: dict[str, list[dict]] = defaultdict(list)
        for p in members:
            clusters[_root(parent, p["part_number"])].append(p)
        for group in clusters.values():
            if len(group) < 2:
                continue
            survivor = choose_survivor(group, where_used)
            others = [p["part_number"] for p in group if p is not survivor]
            numbers = {p["part_number"] for p in group}
            sim = min(s for (a, _), s in scores.items() if a in numbers)
            material = next((p["material"] for p in group if p["material"]), "material missing")
            classes = sorted({p["export_class"] or "none" for p in group})
            findings.append(Finding(
                "DQ-DUP", survivor["part_number"], survivor["business_unit"],
                f"{len(group)} parts look like the same {ptype.lower()} ({size}, {material})",
                sorted(numbers),
                {"similarity": round(sim, 2), "block": f"{ptype} · {size}",
                 "units": sorted({p["business_unit"] for p in group}),
                 "where_used": {p["part_number"]: len(where_used.get(p["part_number"], []))
                                for p in group},
                 "export_classes": classes},
                {"action": "merge", "survivor": survivor["part_number"], "retire": others,
                 "why": survivor_reason(survivor, group, where_used),
                 # merging would change some assemblies' export classification
                 "classification_conflict": len(classes) > 1}))
    return findings


def _root(parent: dict[str, str], x: str) -> str:
    """Union-find lookup with path halving."""
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def material_from(description: str, known: set[str]) -> str:
    """When the material field is empty, the description often still says it."""
    words = normalize(description)
    matches = [m for m in known if normalize(m) <= words]
    return max(matches, key=lambda m: len(normalize(m))) if matches else ""


def choose_survivor(group: list[dict], where_used: dict[str, list[str]]) -> dict:
    """Keep the part most used in BOMs, then a released one, then the oldest."""
    return sorted(group, key=lambda p: (-len(where_used.get(p["part_number"], [])),
                                        p["lifecycle"] != "Released", p["created"],
                                        p["part_number"]))[0]


def survivor_reason(s: dict, group: list[dict], where_used: dict[str, list[str]]) -> str:
    uses = len(where_used.get(s["part_number"], []))
    others = max(len(where_used.get(p["part_number"], [])) for p in group if p is not s)
    if uses > others:
        return f"used in the most assemblies ({uses})"
    if s["lifecycle"] == "Released" and any(p["lifecycle"] != "Released" for p in group):
        return "the only released part"
    return f"oldest ({s['created']})"


def analyze(parts: list[dict], bom: list[dict], controlled_units: set[str]) -> list[Finding]:
    by_pn = {p["part_number"]: p for p in parts}
    where_used: dict[str, list[str]] = defaultdict(list)
    for line in bom:
        where_used[line["child"]].append(line["parent"])
    findings = find_duplicates(parts, where_used)

    # export classification: never proposed, always reviewed by Trade Compliance
    controlled_asm = {p["part_number"] for p in parts if p["part_type"] == "Assembly"
                      and p["business_unit"] in controlled_units}
    for p in parts:
        if p["export_class"]:
            continue
        under = sorted({a for a in where_used.get(p["part_number"], []) if a in controlled_asm})
        if p["business_unit"] in controlled_units or under:
            reason = (f"{p['business_unit']} is an export-controlled business unit"
                      if p["business_unit"] in controlled_units else
                      f"used in {len(under)} controlled assembl{'y' if len(under) == 1 else 'ies'}")
            findings.append(Finding(
                "DQ-EXP", p["part_number"], p["business_unit"],
                f"{p['part_number']} has no export classification: {reason}",
                [p["part_number"]] + under,
                {"reason": reason, "controlled_assemblies": under,
                 "lifecycle": p["lifecycle"]}, {}))

    for line in bom:
        parent, child = by_pn.get(line["parent"]), by_pn.get(line["child"])
        if parent is None:
            continue
        if child is None:
            findings.append(Finding(
                "DQ-ORPHAN", parent["part_number"], parent["business_unit"],
                f"{parent['part_number']} find {line['find_number']} points to "
                f"{line['child']}, which does not exist",
                [parent["part_number"], line["child"]],
                {"find_number": line["find_number"], "quantity": line["quantity"],
                 "near_matches": near_matches(line["child"], by_pn)},
                {"action": "remove_line", "parent": parent["part_number"],
                 "find_number": line["find_number"], "child": line["child"]}))
            continue
        if parent["lifecycle"] != "Released":
            continue
        if child["lifecycle"] == "In Work":
            findings.append(Finding(
                "DQ-REL", parent["part_number"], parent["business_unit"],
                f"Released {parent['part_number']} uses In Work {child['part_number']}",
                [parent["part_number"], child["part_number"]],
                {"find_number": line["find_number"], "child_description": child["description"]},
                {"action": "release_child", "part": child["part_number"]}))
        elif child["lifecycle"] == "Obsolete":
            successor = child["superseded_by"]
            findings.append(Finding(
                "DQ-OBS", parent["part_number"], parent["business_unit"],
                f"Released {parent['part_number']} uses obsolete {child['part_number']}",
                [parent["part_number"], child["part_number"]] + ([successor] if successor else []),
                {"find_number": line["find_number"],
                 "superseded_by": successor or "none on record"},
                {"action": "replace_child", "parent": parent["part_number"],
                 "find_number": line["find_number"], "old": child["part_number"],
                 "new": successor}))

    lines_by_child: dict[str, list[dict]] = defaultdict(list)
    for line in bom:
        lines_by_child[line["child"]].append(line)
    for pn, lines in lines_by_child.items():
        part = by_pn.get(pn)
        if part is None:
            continue
        wrong = [ln for ln in lines if ln["uom"] != part["uom"]]
        if wrong:
            findings.append(Finding(
                "DQ-UOM", pn, part["business_unit"],
                f"{pn} is {part['uom']} in the part master but "
                f"{', '.join(sorted({ln['uom'] for ln in wrong}))} on {len(wrong)} BOM "
                f"line{'' if len(wrong) == 1 else 's'}",
                [pn] + sorted({ln["parent"] for ln in wrong}),
                {"part_uom": part["uom"],
                 "lines": [f"{ln['parent']} find {ln['find_number']}: {ln['quantity']} "
                           f"{ln['uom']}" for ln in wrong]},
                {"action": "set_line_uom", "uom": part["uom"],
                 "lines": [[ln["parent"], ln["find_number"]] for ln in wrong]}))

    for p in parts:
        if p["part_type"] == "Assembly":
            continue
        missing = [f for f, needed in (("material", True),
                                        ("weight_kg", p["make_buy"] == "buy")) if needed
                   and not p[f]]
        if missing:
            findings.append(Finding(
                "DQ-ATTR", p["part_number"], p["business_unit"],
                f"{p['part_number']} is missing "
                f"{' and '.join(m.replace('_kg', '') for m in missing)}",
                [p["part_number"]], {"missing": missing, "description": p["description"]},
                {"action": "set_attribute", "fields": missing}))

    order = {r: i for i, r in enumerate(RULES)}
    findings.sort(key=lambda f: (order[f.rule], f.business_unit, f.part_number))
    return findings


def near_matches(missing: str, by_pn: dict[str, dict]) -> list[str]:
    """Existing part numbers one or two characters away: likely what was meant."""
    prefix = missing[:-2]
    return sorted(pn for pn in by_pn if pn.startswith(prefix) and pn != missing)[:3]


def detection(findings: list[Finding], planted: dict) -> dict[str, dict]:
    """Recall and precision against the problems the demo generator planted."""
    out = {}
    found_dups = [set(f.parts) for f in findings if f.rule == "DQ-DUP"]
    hit = sum(any(set(c) <= g for g in found_dups) for c in planted["DQ-DUP"])
    true = sum(any(set(c) <= g for c in planted["DQ-DUP"]) for g in found_dups)
    out["DQ-DUP"] = {"planted": len(planted["DQ-DUP"]), "found": hit,
                     "flagged": len(found_dups), "matching": true}
    single = {"DQ-EXP": lambda f: f.part_number, "DQ-ATTR": lambda f: f.part_number,
              "DQ-UOM": lambda f: f.part_number,
              "DQ-REL": lambda f: tuple(f.parts[:2]), "DQ-OBS": lambda f: tuple(f.parts[:2]),
              "DQ-ORPHAN": lambda f: tuple(f.parts[:2])}
    for rule, key in single.items():
        want = {tuple(x) if isinstance(x, list) else x for x in planted[rule]}
        got = {key(f) for f in findings if f.rule == rule}
        out[rule] = {"planted": len(want), "found": len(want & got), "flagged": len(got),
                     "matching": len(want & got)}
    return out
