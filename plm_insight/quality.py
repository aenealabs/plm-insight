"""Datasets, findings, and decisions: the data steward's queue.

Each imported export is a dataset. Rules run on import and every finding becomes an issue.
A person accepts a proposed fix (it joins the change list for a PLM bulk update) or rejects
it with a reason. The tool never writes to PLM, and it never sets export classification:
those issues go to a Trade Compliance queue, where only that role decides.

Decisions carry over to the next import by fingerprint (rule + parts): a rejected false
positive stays rejected, and a fix accepted but still present in the new export is reopened.
"""

import csv
import io
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, select

from plm_insight.db import audit, utcnow
from plm_insight.rules import RULE_NAMES, RULES, analyze, detection

PART_FIELDS = ["part_number", "description", "part_type", "business_unit", "material", "size",
               "uom", "weight_kg", "make_buy", "lifecycle", "export_class", "superseded_by",
               "created", "created_by"]
BOM_FIELDS = ["parent", "find_number", "child", "quantity", "uom"]
OPEN, ACCEPTED, REJECTED = "open", "accepted", "rejected"
COMPLIANCE_RULES = {"DQ-EXP"}


class ComplianceRequired(PermissionError):
    """Only Trade Compliance may do this; the caller audits the refusal."""


class QualityError(ValueError):
    """Invalid import or decision; the message is user-facing."""


class Dataset(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    parts: int
    bom_lines: int
    controlled_units: str = "[]"
    detection: str = ""  # demo only: rules measured against the planted problems
    loaded_by: str
    loaded_at: datetime = Field(default_factory=utcnow)

    @property
    def controlled(self) -> list[str]:
        return json.loads(self.controlled_units)

    @property
    def detection_data(self) -> dict | None:
        return json.loads(self.detection) if self.detection else None


class Part(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    dataset_id: int = Field(foreign_key="dataset.id", index=True)
    part_number: str = Field(index=True)
    description: str
    part_type: str
    business_unit: str
    material: str = ""
    size: str = ""
    uom: str = "EA"
    weight_kg: str = ""
    make_buy: str = ""
    lifecycle: str = ""
    export_class: str = ""
    superseded_by: str = ""
    created: str = ""
    created_by: str = ""


class BomLine(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    dataset_id: int = Field(foreign_key="dataset.id", index=True)
    parent: str = Field(index=True)
    find_number: str
    child: str = Field(index=True)
    quantity: str
    uom: str


class Issue(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    dataset_id: int = Field(foreign_key="dataset.id", index=True)
    rule: str = Field(index=True)
    severity: str
    business_unit: str = Field(index=True)
    part_number: str = Field(index=True)
    title: str
    parts: str  # JSON list
    evidence: str  # JSON
    fix: str  # JSON; {} for compliance reviews
    fingerprint: str = Field(index=True)
    status: str = OPEN
    value: str = ""  # what the decider entered (survivor, attribute value, classification)
    reason: str = ""
    decided_by: str = ""
    decided_at: datetime | None = None
    note: str = ""  # e.g. reopened because an accepted fix is still present

    @property
    def key(self) -> str:
        return f"ISS-{self.id}"

    @property
    def part_list(self) -> list[str]:
        return json.loads(self.parts)

    @property
    def evidence_data(self) -> dict:
        return json.loads(self.evidence)

    @property
    def fix_data(self) -> dict:
        return json.loads(self.fix)

    @property
    def queue(self) -> str:
        return "compliance" if self.rule in COMPLIANCE_RULES else "steward"


def read_csv(text: str, needed: list[str], what: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    have = {h.strip() for h in reader.fieldnames or []}
    missing = [f for f in needed if f not in have]
    if missing:
        raise QualityError(f"The {what} file is missing columns: {', '.join(missing)}.")
    rows = [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in reader]
    if not rows:
        raise QualityError(f"The {what} file has no rows.")
    return rows


class QualityStore:
    def __init__(self, engine: Engine):
        self.engine = engine

    # ---------- import ----------

    def seed_if_empty(self, dataset_dir: Path, actor: str) -> None:
        with Session(self.engine) as s:
            if s.exec(select(Dataset)).first() is not None:
                return
        meta = json.loads((dataset_dir / "dataset.json").read_text())
        planted = dataset_dir / "planted.json"
        self.load((dataset_dir / "parts.csv").read_text(), (dataset_dir / "bom.csv").read_text(),
                  meta["name"], meta["controlled_business_units"], actor,
                  json.loads(planted.read_text()) if planted.exists() else None)

    def load(self, parts_csv: str, bom_csv: str, name: str, controlled: list[str],
             actor: str, planted: dict | None = None) -> Dataset:
        parts = read_csv(parts_csv, ["part_number", "description", "part_type", "business_unit",
                                     "lifecycle"], "parts")
        for p in parts:
            for f in PART_FIELDS:
                p.setdefault(f, "")
            p["uom"] = p["uom"] or "EA"
        numbers = Counter(p["part_number"] for p in parts)
        if dup := [n for n, c in numbers.items() if c > 1][:3]:
            raise QualityError(f"Part numbers appear more than once: {', '.join(dup)}.")
        bom = read_csv(bom_csv, BOM_FIELDS, "BOM")
        findings = analyze(parts, bom, set(controlled))
        previous = self.dataset()
        prior = self._decisions(previous.id) if previous else {}
        with Session(self.engine) as s:
            ds = Dataset(name=name, parts=len(parts), bom_lines=len(bom),
                         controlled_units=json.dumps(controlled), loaded_by=actor,
                         detection=json.dumps(detection(findings, planted)) if planted else "")
            s.add(ds)
            s.flush()
            s.add_all(Part(dataset_id=ds.id, **{f: p[f] for f in PART_FIELDS}) for p in parts)
            s.add_all(BomLine(dataset_id=ds.id, **{f: b[f] for f in BOM_FIELDS}) for b in bom)
            carried = reopened = 0
            for f in findings:
                fp = f"{f.rule}:{','.join(sorted(f.parts))}"
                issue = Issue(dataset_id=ds.id, rule=f.rule, severity=f.severity,
                              business_unit=f.business_unit, part_number=f.part_number,
                              title=f.title, parts=json.dumps(f.parts),
                              evidence=json.dumps(f.evidence), fix=json.dumps(f.fix),
                              fingerprint=fp)
                if fp in prior:
                    old = prior[fp]
                    if old.status == REJECTED:
                        issue.status, issue.reason = REJECTED, old.reason
                        issue.decided_by, issue.decided_at = old.decided_by, old.decided_at
                        issue.note = f"Rejection carried over from dataset {old.dataset_id}."
                        carried += 1
                    elif old.status == ACCEPTED:
                        issue.note = (f"Reopened: a fix was accepted by {old.decided_by} on "
                                      f"{old.decided_at:%Y-%m-%d} but the problem is still in "
                                      "this export.")
                        reopened += 1
                s.add(issue)
            audit(s, actor, "dataset_loaded",
                  f"{name}: {len(parts)} parts, {len(bom)} BOM lines, {len(findings)} findings"
                  + (f"; {carried} rejections carried over" if carried else "")
                  + (f"; {reopened} accepted fixes reopened" if reopened else ""))
            s.commit()
            s.refresh(ds)
        return ds

    def _decisions(self, dataset_id: int) -> dict[str, Issue]:
        with Session(self.engine) as s:
            rows = s.exec(select(Issue).where(Issue.dataset_id == dataset_id,
                                              Issue.status != OPEN)).all()
        return {i.fingerprint: i for i in rows}

    # ---------- reads ----------

    def dataset(self) -> Dataset | None:
        with Session(self.engine) as s:
            return s.exec(select(Dataset).order_by(Dataset.id.desc())).first()

    def datasets(self) -> list[Dataset]:
        with Session(self.engine) as s:
            return list(s.exec(select(Dataset).order_by(Dataset.id)))

    def issues(self, rule: str = "", unit: str = "", status: str = "", queue: str = "",
               dataset_id: int | None = None) -> list[Issue]:
        ds_id = dataset_id or (self.dataset().id if self.dataset() else 0)
        with Session(self.engine) as s:
            q = select(Issue).where(Issue.dataset_id == ds_id).order_by(Issue.id)
            if rule:
                q = q.where(Issue.rule == rule)
            if unit:
                q = q.where(Issue.business_unit == unit)
            if status:
                q = q.where(Issue.status == status)
            if queue == "compliance":
                q = q.where(Issue.rule.in_(COMPLIANCE_RULES))
            elif queue == "steward":
                q = q.where(Issue.rule.not_in(COMPLIANCE_RULES))
            return list(s.exec(q))

    def issue(self, issue_id: int) -> Issue:
        with Session(self.engine) as s:
            issue = s.get(Issue, issue_id)
        if issue is None:
            raise QualityError("That issue does not exist.")
        return issue

    def parts(self, numbers: list[str], dataset_id: int) -> dict[str, Part]:
        with Session(self.engine) as s:
            rows = s.exec(select(Part).where(Part.dataset_id == dataset_id,
                                             Part.part_number.in_(numbers))).all()
        return {p.part_number: p for p in rows}

    def where_used(self, number: str, dataset_id: int) -> list[BomLine]:
        with Session(self.engine) as s:
            return list(s.exec(select(BomLine).where(BomLine.dataset_id == dataset_id,
                                                     BomLine.child == number)
                               .order_by(BomLine.parent)))

    def children(self, number: str, dataset_id: int) -> list[BomLine]:
        with Session(self.engine) as s:
            return list(s.exec(select(BomLine).where(BomLine.dataset_id == dataset_id,
                                                     BomLine.parent == number)
                               .order_by(BomLine.id)))

    def issues_for_part(self, number: str, dataset_id: int) -> list[Issue]:
        with Session(self.engine) as s:
            rows = s.exec(select(Issue).where(Issue.dataset_id == dataset_id,
                                              Issue.parts.contains(f'"{number}"'))).all()
        return list(rows)

    # ---------- decisions ----------

    def decide(self, issue_id: int, decision: str, actor: str, *, compliance: bool,
               reason: str = "", value: str = "") -> Issue:
        reason, value = reason.strip(), value.strip()
        with Session(self.engine) as s:
            issue = s.get(Issue, issue_id)
            if issue is None:
                raise QualityError("That issue does not exist.")
            if issue.queue == "compliance" and not compliance:
                raise ComplianceRequired("Only Trade Compliance can decide export "
                                         "classification.")
            if (decision == ACCEPTED and issue.fix_data.get("classification_conflict")
                    and not compliance):
                raise ComplianceRequired("These parts have different export classifications, "
                                         "so merging them changes the classification wherever "
                                         "the retired parts are used. Trade Compliance must "
                                         "approve this merge.")
            if issue.status != OPEN:
                raise QualityError(f"{issue.key} was already {issue.status} by "
                                   f"{issue.decided_by}.")
            if decision == REJECTED and not reason:
                raise QualityError("Say why the finding is wrong, so it is not raised again "
                                   "without a reason.")
            if decision == ACCEPTED:
                value = self._validate_value(issue, value)
                proposed = self.proposed_value(issue)
                if proposed and value != proposed and not reason:
                    raise QualityError(f"The rule proposes {proposed}. Say why you chose "
                                       f"{value}.")
            elif decision != REJECTED:
                raise QualityError("Choose accept or reject.")
            issue.status, issue.reason, issue.value = decision, reason, value
            issue.decided_by, issue.decided_at = actor, utcnow()
            s.add(issue)
            audit(s, actor, f"issue_{decision}",
                  f"{issue.key} {issue.rule} {issue.part_number}"
                  + (f" = {value}" if value else "") + (f": {reason}" if reason else ""))
            s.commit()
            s.refresh(issue)
        return issue

    @staticmethod
    def proposed_value(issue: Issue) -> str:
        """The fix the rule proposes, where it proposes one (the part to keep, or the
        successor); choosing a different value needs a reason."""
        fix = issue.fix_data
        return {"DQ-DUP": fix.get("survivor", ""), "DQ-OBS": fix.get("new", "")}.get(
            issue.rule, "") or ""

    def _validate_value(self, issue: Issue, value: str) -> str:
        fix = issue.fix_data
        if issue.rule == "DQ-EXP":
            if not value:
                raise QualityError("Enter the export classification (for example EAR99 or "
                                   "an ECCN).")
            return value.upper()
        if issue.rule == "DQ-DUP":
            value = value or fix["survivor"]
            if value not in issue.part_list:
                raise QualityError("The part to keep must be one of the duplicates.")
            return value
        if issue.rule == "DQ-OBS":
            value = value or fix.get("new", "")
            if not value:
                raise QualityError("No successor is on record. Enter the replacement part.")
            if not self.parts([value], issue.dataset_id):
                raise QualityError(f"{value} is not in this export.")
            return value
        if issue.rule == "DQ-ATTR":
            if not value:
                raise QualityError(f"Enter the {' and '.join(fix['fields'])}.")
            return value
        return value

    # ---------- outputs ----------

    def change_list(self) -> list[dict]:
        """Accepted decisions as rows for a PLM bulk update. Nothing is written to PLM here."""
        ds = self.dataset()
        rows = []
        for i in self.issues(status=ACCEPTED, dataset_id=ds.id if ds else None):
            fix, base = i.fix_data, {"issue": i.key, "rule": i.rule, "decided_by": i.decided_by,
                                     "decided_at": f"{i.decided_at:%Y-%m-%d %H:%M}"}
            if i.rule == "DQ-DUP":
                for other in [p for p in i.part_list if p != i.value]:
                    for line in self.where_used(other, i.dataset_id):
                        rows.append(base | {"object": f"BOM {line.parent} find {line.find_number}",
                                            "action": "replace child", "old": other,
                                            "new": i.value})
                    rows.append(base | {"object": other, "action": "set lifecycle",
                                        "old": "Released", "new": f"Obsolete (use {i.value})"})
            elif i.rule == "DQ-EXP":
                rows.append(base | {"object": i.part_number, "action": "set export_class",
                                    "old": "", "new": i.value})
            elif i.rule == "DQ-OBS":
                rows.append(base | {"object": f"BOM {fix['parent']} find {fix['find_number']}",
                                    "action": "replace child", "old": fix["old"], "new": i.value})
            elif i.rule == "DQ-REL":
                rows.append(base | {"object": fix["part"], "action": "release (route for "
                                    "approval)", "old": "In Work", "new": "Released"})
            elif i.rule == "DQ-ORPHAN":
                rows.append(base | {"object": f"BOM {fix['parent']} find {fix['find_number']}",
                                    "action": "remove line", "old": fix["child"], "new": ""})
            elif i.rule == "DQ-UOM":
                for parent, find in fix["lines"]:
                    rows.append(base | {"object": f"BOM {parent} find {find}",
                                        "action": "set uom", "old": "", "new": fix["uom"]})
            elif i.rule == "DQ-ATTR":
                rows.append(base | {"object": i.part_number,
                                    "action": f"set {', '.join(fix['fields'])}", "old": "",
                                    "new": i.value})
        return rows

    def scorecard(self) -> dict:
        ds = self.dataset()
        if ds is None:
            return {}
        issues = self.issues(dataset_id=ds.id)
        with Session(self.engine) as s:
            unit_parts = Counter(s.exec(select(Part.business_unit)
                                        .where(Part.dataset_id == ds.id)).all())
        flagged: dict[str, set] = defaultdict(set)  # unit -> parts with an open issue
        by_unit: dict[str, Counter] = defaultdict(Counter)
        by_rule: dict[str, Counter] = defaultdict(Counter)
        for i in issues:
            by_unit[i.business_unit][i.status] += 1
            by_rule[i.rule][i.status] += 1
            if i.status == OPEN:
                flagged[i.business_unit].add(i.part_number)
        units = []
        for unit, total in sorted(unit_parts.items()):
            score = 100 * (1 - len(flagged[unit]) / total) if total else 100
            units.append({"unit": unit, "parts": total, "open": by_unit[unit][OPEN],
                          "accepted": by_unit[unit][ACCEPTED],
                          "rejected": by_unit[unit][REJECTED], "score": round(score, 1)})
        all_flagged = set().union(*flagged.values()) if flagged else set()
        rules = [{"rule": r, "name": RULE_NAMES[r], "severity": RULES[r][0], "text": RULES[r][1],
                  "open": by_rule[r][OPEN], "accepted": by_rule[r][ACCEPTED],
                  "rejected": by_rule[r][REJECTED]} for r in RULES]
        return {"dataset": ds, "units": units, "rules": rules,
                "score": round(100 * (1 - len(all_flagged) / ds.parts), 1) if ds.parts else 100,
                "open": sum(r["open"] for r in rules),
                "decided": sum(r["accepted"] + r["rejected"] for r in rules)}

    def trend(self) -> list[dict]:
        out = []
        for ds in self.datasets():
            issues = self.issues(dataset_id=ds.id)
            out.append({"dataset": ds, "findings": len(issues),
                        "open": sum(i.status == OPEN for i in issues)})
        return out
