"""Generate the fictional engineering change (ECO) history used by the offline demo.

Deterministic (seeded). Writes data/demo/ecos.csv (one row per ECO), eco_events.csv (one row
per stage visit, with the reviewer and any return comment), and eco_planted.json (the
patterns planted, used to check the analytics find them). All names and ECOs are invented.

Usage: python scripts/generate_ecos.py
"""

import csv
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "demo"
rng = random.Random(20261019)
AS_OF = datetime(2026, 10, 1, 9, 0)  # the export date
START = datetime(2025, 1, 6, 9, 0)

UNITS = {"Electrical Products": 1.1, "Aerospace Systems": 1.35, "eMobility": 0.9,
         "Industrial Controls": 1.0}  # how slow each unit's reviews run
UNIT_WEIGHTS = [0.35, 0.2, 0.25, 0.2]
TYPES = {"Design change": 0.45, "Document change": 0.25, "Part obsolescence": 0.15,
         "Supplier change": 0.15}
REVIEWERS = {  # technical reviewers by unit
    "Electrical Products": ["P. Raman", "S. Iqbal", "T. Novak"],
    "Aerospace Systems": ["M. Bell", "J. Ortega"],
    "eMobility": ["G. Whitfield", "L. Chen"],
    "Industrial Controls": ["R. Silva", "N. Ahmed"],
}
BOARD = {  # change board approvers by unit
    "Electrical Products": ["K. Watanabe", "D. Moss"],
    "Aerospace Systems": ["M. Okonkwo", "R. Tanaka"],
    "eMobility": ["A. Lund", "C. Ferreira"],
    "Industrial Controls": ["B. Hughes", "E. Varga"],
}
BOTTLENECK = "M. Okonkwo"  # planted: holds ECOs far longer than other board approvers
IMPLEMENTERS = ["H. Liu", "E. Kovacs", "F. Diaz", "Y. Park"]
ORIGINATORS = ["A. Novak", "B. Osei", "C. Lindqvist", "D. Mehta", "E. Duarte", "F. Haddad",
               "G. Brennan", "H. Sato", "I. Moreau", "J. Adeyemi", "K. Petrov", "L. Fischer"]
MEDIAN_DAYS = {"Technical Review": 4.5, "Change Board": 5.0, "Implementation": 5.5,
               "Rework": 4.0}

# Return reasons (planted shares) and how reviewers actually phrase them.
REASONS = {
    "affected parts": (0.36, [
        "Affected parts list is missing the fasteners at find {f}; please add them.",
        "Only the top assembly is listed as affected. Add the child parts that change.",
        "Where-used not attached, so we cannot see the impact on {asm}.",
        "Affected items incomplete: {pn} is changed but not listed.",
        "Please list every affected part, not just the drawing.",
    ]),
    "cost impact": (0.24, [
        "No cost impact estimate. Plants need it for inventory disposition.",
        "Cost impact section is blank.",
        "Add scrap and rework cost for existing stock of {pn}.",
        "Missing cost impact; board will not review without it.",
    ]),
    "drawing not updated": (0.16, [
        "Drawing for {pn} is still at the old revision; attach the updated drawing.",
        "Markup attached but the released drawing is not updated.",
        "Drawing and model disagree on the hole pattern; update the drawing.",
    ]),
    "effectivity": (0.12, [
        "Effectivity date conflicts with the SAP cutover; pick a date after it.",
        "Effectivity by serial number is missing for the field units.",
        "Effectivity date is before supplier stock runs out; align with purchasing.",
    ]),
    "export classification": (0.07, [
        "New part {pn} has no export classification; route to Trade Compliance first.",
        "Export review missing for the new bracket before release.",
    ]),
    "wrong change type": (0.05, [
        "This is a document-only change; resubmit as a document change.",
        "Change type should be supplier change, not design change.",
    ]),
}


def days(median: float, factor: float) -> timedelta:
    """Right-skewed duration around a median (log-normal)."""
    return timedelta(days=max(0.1, median * factor * rng.lognormvariate(0, 0.55)))


def comment(reason: str) -> str:
    text = rng.choice(REASONS[reason][1])
    return text.format(f=rng.choice([10, 20, 30, 40, 60]),
                       asm=f"100-00{rng.randrange(8000, 8999)}",
                       pn=f"{rng.choice([100, 210, 330, 450])}-00{rng.randrange(4100, 8999)}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ecos, events = [], []
    reasons, weights = list(REASONS), [v[0] for v in REASONS.values()]
    created = START
    n = 0
    while created < AS_OF - timedelta(days=1):
        n += 1
        unit = rng.choices(list(UNITS), UNIT_WEIGHTS)[0]
        ctype = rng.choices(list(TYPES), list(TYPES.values()))[0]
        expedite = rng.random() < 0.08
        factor = UNITS[unit] * (0.5 if expedite else 1.0) * (0.7 if ctype == "Document change"
                                                             else 1.0)
        eco = {"eco": f"ECO-{created:%y}-{n:05d}", "title": "", "business_unit": unit,
               "change_type": ctype, "priority": "expedite" if expedite else "normal",
               "originator": rng.choice(ORIGINATORS),
               "affected_parts": str(rng.choice([1, 2, 3, 4, 6, 8, 12])),
               "created": created.isoformat(timespec="minutes"), "status": "", "released": ""}
        eco["title"] = rng.choice({
            "Design change": ["Change hole pattern on {x}", "Increase wall thickness on {x}",
                              "Replace fastener on {x}", "Add ground strap to {x}"],
            "Document change": ["Correct note on drawing for {x}",
                                "Update tolerance callout on {x}"],
            "Part obsolescence": ["Replace obsolete part on {x}", "Last-time buy for {x}"],
            "Supplier change": ["Second source for {x}", "Move {x} to new supplier"],
        }[ctype]).format(x=rng.choice(["panel assembly", "breaker housing", "charger cover",
                                       "drive bracket", "busbar kit", "actuator mount",
                                       "controller enclosure", "contactor base"]))
        t = created
        seq = 0
        stages = ["Technical Review", "Change Board", "Implementation"]
        i = 0
        returns = 0
        status = "Released"
        while i < len(stages):
            stage = stages[i]
            actor = (rng.choice(REVIEWERS[unit]) if stage == "Technical Review"
                     else rng.choice(BOARD[unit]) if stage == "Change Board"
                     else rng.choice(IMPLEMENTERS))
            slow = 2.6 if actor == BOTTLENECK else 1.0
            left = t + days(MEDIAN_DAYS[stage], factor * slow)
            seq += 1
            # about a third of ECOs are returned at least once, mostly in review or board
            p_return = {"Technical Review": 0.22, "Change Board": 0.12}.get(stage, 0)
            p_return *= 0.45 if returns else 1.0
            outcome, note = "approved", ""
            if rng.random() < p_return:
                outcome, note = "returned", comment(rng.choices(reasons, weights)[0])
            if left > AS_OF:
                events.append({"eco": eco["eco"], "seq": str(seq), "stage": stage,
                               "actor": actor, "entered": t.isoformat(timespec="minutes"),
                               "left": "", "outcome": "in progress", "comment": ""})
                status = stage
                break
            events.append({"eco": eco["eco"], "seq": str(seq), "stage": stage, "actor": actor,
                           "entered": t.isoformat(timespec="minutes"),
                           "left": left.isoformat(timespec="minutes"), "outcome": outcome,
                           "comment": note})
            t = left
            if outcome == "returned":
                returns += 1
                rework_end = t + days(MEDIAN_DAYS["Rework"], 1.0)
                seq += 1
                if rework_end > AS_OF:
                    events.append({"eco": eco["eco"], "seq": str(seq), "stage": "Rework",
                                   "actor": eco["originator"],
                                   "entered": t.isoformat(timespec="minutes"), "left": "",
                                   "outcome": "in progress", "comment": ""})
                    status = "Rework"
                    break
                events.append({"eco": eco["eco"], "seq": str(seq), "stage": "Rework",
                               "actor": eco["originator"],
                               "entered": t.isoformat(timespec="minutes"),
                               "left": rework_end.isoformat(timespec="minutes"),
                               "outcome": "resubmitted", "comment": ""})
                t = rework_end
                i = 0  # back to technical review
                continue
            i += 1
        eco["status"] = status
        if status == "Released":
            eco["released"] = t.isoformat(timespec="minutes")
        ecos.append(eco)
        created += timedelta(hours=rng.expovariate(1 / 10.5))  # ~2.3 ECOs per day

    with (OUT / "ecos.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(ecos[0]))
        w.writeheader()
        w.writerows(ecos)
    with (OUT / "eco_events.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(events[0]))
        w.writeheader()
        w.writerows(events)
    (OUT / "eco_planted.json").write_text(json.dumps({
        "_note": "Patterns planted by scripts/generate_ecos.py.",
        "as_of": AS_OF.isoformat(timespec="minutes"), "bottleneck_approver": BOTTLENECK,
        "return_reason_shares": {k: v[0] for k, v in REASONS.items()},
        "slowest_unit": max(UNITS, key=UNITS.get)}, indent=1) + "\n")
    returned = len({e["eco"] for e in events if e["outcome"] == "returned"})
    print(f"{len(ecos)} ECOs, {len(events)} events; {returned} returned at least once "
          f"({100 * returned / len(ecos):.0f}%); open: "
          f"{sum(e['status'] != 'Released' for e in ecos)}")


if __name__ == "__main__":
    main()
