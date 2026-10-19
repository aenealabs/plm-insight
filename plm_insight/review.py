"""AI reviews of findings, checked by code before anyone relies on them.

For a duplicate cluster the model says whether the parts are the same and which to keep; for
a missing export classification it assembles evidence for Trade Compliance. Either way every
point cites part numbers, fields, and values, and code checks each against the export. A
compliance packet may never contain a classification: anything that looks like one is
removed and the removal is shown.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, select

from plm_insight.db import audit, utcnow
from plm_insight.models import CompliancePacket, DuplicateReview, Point
from plm_insight.quality import PART_FIELDS, Issue, QualityStore

INPUT_FIELDS = [f for f in PART_FIELDS if f != "superseded_by"]
# ECCNs (9A991, 3A001.a), EAR99, USML / ITAR categories (Category XI, USML XI(c))
CLASSIFICATION = re.compile(
    r"\b\d[A-E]\d{3}(?:\.[a-z0-9.]+)?\b|\bEAR\s?99\b|\bUSML\b[^.;]*|\bITAR\s+Category\s+[IVXL]+\b"
    r"|\bCategory\s+[IVXL]+\b(?:\([a-z]\))?", re.IGNORECASE)
REMOVED = "[classification removed]"
KINDS = {"DQ-DUP": "duplicates", "DQ-EXP": "compliance"}


class AiReview(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    issue_id: int = Field(foreign_key="issue.id", index=True)
    kind: str
    input_text: str
    output: str  # JSON as returned (after removal of classifications for compliance packets)
    removed: int = 0  # classification-like strings removed from a compliance packet
    drafted_by: str
    requested_by: str
    created_at: datetime = Field(default_factory=utcnow)


# ---------- input ----------

def part_row(p, used_in: int) -> dict:
    row = {f: getattr(p, f) for f in INPUT_FIELDS}
    row["used_in"] = str(used_in)
    return row


def review_input(store: QualityStore, issue: Issue) -> str:
    """What the model sees, as JSON so every value is unambiguous."""
    ds = issue.dataset_id
    if issue.rule == "DQ-DUP":
        parts = store.parts(issue.part_list, ds)
        rows = [part_row(parts[pn], len(store.where_used(pn, ds))) for pn in issue.part_list]
        return json.dumps({"finding": issue.title, "parts": rows}, indent=1)
    if issue.rule == "DQ-EXP":
        part = store.parts([issue.part_number], ds)[issue.part_number]
        ev = issue.evidence_data
        assemblies = store.parts(ev.get("controlled_assemblies", []), ds)
        return json.dumps({
            "finding": issue.title,
            "part": part_row(part, len(store.where_used(part.part_number, ds))),
            "controlled_assemblies_using_it": [
                {f: getattr(a, f) for f in ("part_number", "description", "business_unit",
                                            "lifecycle")} for a in assemblies.values()],
        }, indent=1)
    raise ValueError(f"No AI review for {issue.rule}.")


def input_values(input_text: str) -> dict[str, dict[str, str]]:
    data = json.loads(input_text)
    rows = data.get("parts") or [data["part"]] + data.get("controlled_assemblies_using_it", [])
    return {r["part_number"]: r for r in rows}


# ---------- checks ----------

def _same(a: str, b: str) -> bool:
    return " ".join(str(a).split()).lower() == " ".join(str(b).split()).lower()


@dataclass
class CheckedPoint:
    point: Point
    problems: list[str] = field(default_factory=list)


def check_points(points: list[Point], values: dict[str, dict[str, str]]) -> list[CheckedPoint]:
    out = []
    for p in points:
        problems = []
        if not p.cites:
            problems.append("Cites no values.")
        for c in p.cites:
            row = values.get(c.part_number)
            if row is None:
                problems.append(f"{c.part_number} is not part of this finding.")
            elif c.field not in row:
                problems.append(f"{c.field} was not given for {c.part_number}.")
            elif not _same(row[c.field], c.value):
                shown = row[c.field] or "empty"
                problems.append(f"{c.part_number} {c.field} is {shown}, not "
                                f"{c.value or 'empty'}.")
        out.append(CheckedPoint(p, problems))
    return out


def remove_classifications(packet: CompliancePacket) -> tuple[CompliancePacket, int]:
    count = 0

    def scrub(text: str) -> str:
        nonlocal count
        new, n = CLASSIFICATION.subn(REMOVED, text)
        count += n
        return new

    clean = packet.model_copy(deep=True)
    clean.summary = scrub(clean.summary)
    for p in clean.evidence:
        p.text = scrub(p.text)
    clean.questions = [scrub(q) for q in clean.questions]
    return clean, count


@dataclass
class CheckedReview:
    review: AiReview
    output: DuplicateReview | CompliancePacket
    points: list[CheckedPoint]
    problems: list[str]  # review-level problems

    @property
    def flagged(self) -> int:
        return sum(bool(p.problems) for p in self.points) + len(self.problems)


def checked(review: AiReview, issue: Issue) -> CheckedReview:
    values = input_values(review.input_text)
    if review.kind == "duplicates":
        out = DuplicateReview.model_validate_json(review.output)
        problems = []
        if out.verdict == "same part" and out.keep not in issue.part_list:
            problems.append(f"Proposed keep {out.keep or '(none)'} is not one of the duplicates.")
        return CheckedReview(review, out, check_points(out.reasons, values), problems)
    out = CompliancePacket.model_validate_json(review.output)
    problems = ([f"Removed {review.removed} classification-like "
                 f"string{'' if review.removed == 1 else 's'}: the tool never proposes one."]
                if review.removed else [])
    return CheckedReview(review, out, check_points(out.evidence, values), problems)


# ---------- storage ----------

class ReviewStore:
    def __init__(self, engine: Engine):
        self.engine = engine

    def save(self, issue: Issue, input_text: str, output, actor: str,
             drafted_by: str) -> AiReview:
        removed = 0
        if isinstance(output, CompliancePacket):
            output, removed = remove_classifications(output)
        review = AiReview(issue_id=issue.id, kind=KINDS[issue.rule], input_text=input_text,
                          output=output.model_dump_json(), removed=removed,
                          drafted_by=drafted_by, requested_by=actor)
        with Session(self.engine) as s:
            s.add(review)
            s.flush()
            audit(s, actor, "issue_ai_review",
                  f"{issue.key} {issue.rule}: {KINDS[issue.rule]} review ({drafted_by})"
                  + (f"; removed {removed} classification-like strings" if removed else ""))
            s.commit()
            s.refresh(review)
        return review

    def latest(self, issue_id: int) -> AiReview | None:
        with Session(self.engine) as s:
            return s.exec(select(AiReview).where(AiReview.issue_id == issue_id)
                          .order_by(AiReview.id.desc())).first()
