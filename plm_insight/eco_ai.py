"""Why ECOs are sent back: the model groups return comments into themes; code checks it.

Every ECO id the model cites must be one of the returns it was given, every quote must
appear in that ECO's comment, and no ECO may be cited more often than it was returned.
The counts and shares are computed here from the verified ids, never taken from the model.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, select

from plm_insight.db import audit, utcnow
from plm_insight.eco import EcoEvent
from plm_insight.models import ReturnAnalysis, ReturnTheme


class EcoAiAnalysis(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    eco_dataset_id: int = Field(foreign_key="ecodataset.id", index=True)
    window: str  # e.g. "Apr 2026 to Sep 2026, 148 returns"
    input_text: str
    output: str
    drafted_by: str
    requested_by: str
    created_at: datetime = Field(default_factory=utcnow)


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


@dataclass
class CheckedTheme:
    theme: ReturnTheme
    ecos: list[str]  # verified citations, one per return
    count: int
    share: float
    quotes: list[tuple[str, str, bool]]  # (eco, quote, found in that ECO's comment)
    problems: list[str] = field(default_factory=list)


@dataclass
class CheckedAnalysis:
    analysis: ReturnAnalysis
    record: EcoAiAnalysis
    themes: list[CheckedTheme]
    total: int
    unassigned: list[str]

    @property
    def flagged(self) -> int:
        return sum(bool(t.problems) for t in self.themes) + (1 if self.unassigned else 0)


def check(analysis: ReturnAnalysis, returns: list[EcoEvent],
          record: EcoAiAnalysis) -> CheckedAnalysis:
    """The unit is the return: an ECO sent back twice for different reasons belongs in two
    themes, but no ECO may be cited more times than it was returned."""
    comments: dict[str, list[str]] = {}
    for r in returns:
        comments.setdefault(r.eco, []).append(_norm(r.comment))
    remaining = Counter({eco: len(cs) for eco, cs in comments.items()})
    checked = []
    for t in analysis.themes:
        problems = []
        unknown = sorted({i for i in t.eco_ids if i not in comments})
        if unknown:
            problems.append(f"Cites {len(unknown)} ECO{'' if len(unknown) == 1 else 's'} that "
                            f"were not returned in this period: {', '.join(unknown[:3])}"
                            + ("…" if len(unknown) > 3 else "") + ".")
        mine, over = [], []
        for i in t.eco_ids:
            if i not in comments:
                continue
            if remaining[i] > 0:
                remaining[i] -= 1
                mine.append(i)
            else:
                over.append(i)
        if over:
            problems.append(f"Cited more times than returned: {', '.join(sorted(set(over))[:3])}"
                            + ("…" if len(set(over)) > 3 else "") + ".")
        quotes = [(q.eco, q.quote, q.eco in comments
                   and any(_norm(q.quote) in c for c in comments[q.eco])) for q in t.quotes]
        bad = [q for q in quotes if not q[2]]
        if bad:
            problems.append(f"{len(bad)} quote{'' if len(bad) == 1 else 's'} not found in "
                            "the cited ECO's comment.")
        checked.append(CheckedTheme(t, mine, len(mine),
                                    round(100 * len(mine) / len(returns), 1) if returns else 0.0,
                                    quotes, problems))
    checked.sort(key=lambda c: -c.count)
    unassigned = sorted(e for e, n in remaining.items() for _ in range(n))
    return CheckedAnalysis(analysis, record, checked, len(returns), unassigned)


class EcoAiStore:
    def __init__(self, engine: Engine):
        self.engine = engine

    def save(self, dataset_id: int, window: str, input_text: str, output: ReturnAnalysis,
             actor: str, drafted_by: str) -> EcoAiAnalysis:
        rec = EcoAiAnalysis(eco_dataset_id=dataset_id, window=window, input_text=input_text,
                            output=output.model_dump_json(), drafted_by=drafted_by,
                            requested_by=actor)
        with Session(self.engine) as s:
            s.add(rec)
            audit(s, actor, "eco_ai_review", f"Return themes for {window} ({drafted_by})")
            s.commit()
            s.refresh(rec)
        return rec

    def latest(self, dataset_id: int) -> EcoAiAnalysis | None:
        with Session(self.engine) as s:
            return s.exec(select(EcoAiAnalysis)
                          .where(EcoAiAnalysis.eco_dataset_id == dataset_id)
                          .order_by(EcoAiAnalysis.id.desc())).first()
