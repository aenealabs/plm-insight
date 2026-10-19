"""Engineering change (ECO) analytics: where ECOs wait, who holds them, and why they bounce.

Every number here is computed from the ECO export (one row per ECO, one row per stage visit).
Warnings are rules with their thresholds shown. The model only groups return comments into
themes (review_returns), and code checks its citations and does the counting.
"""

import csv
import io
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, select

from plm_insight.db import audit, utcnow
from plm_insight.quality import QualityError, read_csv

STAGES = ["Technical Review", "Change Board", "Implementation", "Rework"]
# Every threshold the ECO rules use; the rule text, warnings, and Help reference are built
# from these, so changing one changes what the rules do and what they say.
SLOW_SHARE = 0.8  # slow means longer than this share of past visits (or released ECOs) took
BOTTLENECK_FACTOR = 2  # an approver's median at least this many times the stage median
BOTTLENECK_MIN_ECOS = 10  # ... over at least this many ECOs
_TIMES = {2: "twice", 3: "three times"}
_SLOW = f"{round(100 * SLOW_SHARE)}%"

ECO_RULES = {
    "ECO-SLOW-STAGE": f"Open ECO has been in its current stage longer than {_SLOW} of past "
                      "visits to that stage took",
    "ECO-SLOW-TOTAL": f"Open ECO is older than {_SLOW} of released ECOs took end to end",
    "ECO-BOTTLENECK": "Approver's median time in a stage is at least "
                      f"{_TIMES.get(BOTTLENECK_FACTOR, f'{BOTTLENECK_FACTOR} times')} the stage "
                      f"median, over {BOTTLENECK_MIN_ECOS} or more ECOs",
}


class EcoDataset(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    as_of: datetime  # the export date: ages are measured to here
    ecos: int
    events: int
    loaded_by: str
    loaded_at: datetime = Field(default_factory=utcnow)


class Eco(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    dataset_id: int = Field(foreign_key="ecodataset.id", index=True)
    eco: str = Field(index=True)
    title: str
    business_unit: str
    change_type: str
    priority: str
    originator: str
    affected_parts: int
    created: datetime
    status: str
    released: datetime | None = None


class EcoEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    dataset_id: int = Field(foreign_key="ecodataset.id", index=True)
    eco: str = Field(index=True)
    seq: int
    stage: str
    actor: str
    entered: datetime
    left: datetime | None = None
    outcome: str
    comment: str = ""

    def dwell(self, as_of: datetime) -> float:
        return ((self.left or as_of) - self.entered).total_seconds() / 86400


def median(xs: list[float]) -> float:
    return round(statistics.median(xs), 1) if xs else 0.0


def p80(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(SLOW_SHARE * len(s)))], 1)


@dataclass
class EcoAnalytics:
    as_of: datetime
    released: int
    open: list[dict]
    cycle_median: float
    cycle_p80: float
    return_rate: float
    returned: int
    reviewed: int
    by_unit: list[dict]
    stage_unit: dict[str, dict[str, float]]  # stage -> unit -> median days
    stage_median: dict[str, float]
    stage_p80: dict[str, float]
    returns_by_stage: dict[str, int]
    bottlenecks: list[dict]
    warnings: list[dict]
    trend: list[dict]  # by quarter of release: median cycle and count


def analyze(ecos: list[Eco], events: list[EcoEvent], as_of: datetime) -> EcoAnalytics:
    by_eco: dict[str, list[EcoEvent]] = defaultdict(list)
    for e in events:
        by_eco[e.eco].append(e)
    units = sorted({e.business_unit for e in ecos})
    unit_of = {e.eco: e.business_unit for e in ecos}

    cycle = {e.eco: (e.released - e.created).total_seconds() / 86400
             for e in ecos if e.released}
    done_visits = [v for v in events if v.left is not None]
    stage_days: dict[str, list[float]] = defaultdict(list)
    stage_unit_days: dict[tuple, list[float]] = defaultdict(list)
    actor_days: dict[tuple, list[float]] = defaultdict(list)
    for v in done_visits:
        d = v.dwell(as_of)
        stage_days[v.stage].append(d)
        stage_unit_days[(v.stage, unit_of.get(v.eco, ""))].append(d)
        if v.stage != "Rework":
            actor_days[(v.stage, v.actor)].append(d)
    stage_median = {s: median(stage_days[s]) for s in STAGES}
    stage_p80 = {s: p80(stage_days[s]) for s in STAGES}

    returned_ecos = {v.eco for v in events if v.outcome == "returned"}
    reviewed = {v.eco for v in done_visits if v.stage == "Technical Review"}
    returns_by_stage = Counter(v.stage for v in events if v.outcome == "returned")

    by_unit = []
    for u in units:
        mine = [e for e in ecos if e.business_unit == u]
        cyc = [cycle[e.eco] for e in mine if e.eco in cycle]
        rev = [e for e in mine if e.eco in reviewed]
        by_unit.append({"unit": u, "ecos": len(mine), "released": len(cyc),
                        "median": median(cyc), "p80": p80(cyc),
                        "return_rate": round(100 * sum(e.eco in returned_ecos for e in rev)
                                             / len(rev), 1) if rev else 0.0,
                        "open": sum(e.released is None and e.status != "Cancelled"
                                    for e in mine)})

    bottlenecks = []
    for (stage, actor), ds in sorted(actor_days.items()):
        m = median(ds)
        if (len(ds) >= BOTTLENECK_MIN_ECOS and stage_median[stage]
                and m >= BOTTLENECK_FACTOR * stage_median[stage]):
            bottlenecks.append({"stage": stage, "actor": actor, "ecos": len(ds), "median": m,
                                "stage_median": stage_median[stage],
                                "ratio": round(m / stage_median[stage], 1)})
    bottlenecks.sort(key=lambda b: -b["ratio"])

    cycle_p80 = p80(list(cycle.values()))
    open_rows, warnings = [], []
    for e in sorted(ecos, key=lambda x: x.created):
        if e.released is not None or e.status == "Cancelled":
            continue
        visits = sorted(by_eco[e.eco], key=lambda v: v.seq)
        current = visits[-1] if visits else None
        in_stage = round(current.dwell(as_of), 1) if current else 0.0
        age = round((as_of - e.created).total_seconds() / 86400, 1)
        row = {"eco": e.eco, "title": e.title, "unit": e.business_unit,
               "stage": current.stage if current else e.status,
               "actor": current.actor if current else "", "in_stage": in_stage, "age": age,
               "returns": sum(v.outcome == "returned" for v in visits)}
        open_rows.append(row)
        if current and in_stage > stage_p80.get(current.stage, 0):
            warnings.append(row | {"rule": "ECO-SLOW-STAGE",
                                   "why": f"{in_stage} days in {current.stage}; {_SLOW} of visits "
                                          f"finish within {stage_p80[current.stage]}"})
        elif age > cycle_p80:
            warnings.append(row | {"rule": "ECO-SLOW-TOTAL",
                                   "why": f"{age} days old; {_SLOW} of released ECOs finished "
                                          f"within {cycle_p80}"})
    warnings.sort(key=lambda w: -w["in_stage"])

    quarters: dict[str, list[float]] = defaultdict(list)
    for e in ecos:
        if e.released:
            quarters[f"{e.released.year}-Q{(e.released.month - 1) // 3 + 1}"].append(
                cycle[e.eco])
    now_q = f"{as_of.year}-Q{(as_of.month - 1) // 3 + 1}"
    trend = [{"quarter": q, "median": median(ds), "released": len(ds), "partial": q == now_q}
             for q, ds in sorted(quarters.items())]

    return EcoAnalytics(
        as_of=as_of, released=len(cycle), open=open_rows,
        cycle_median=median(list(cycle.values())), cycle_p80=cycle_p80,
        return_rate=round(100 * len(returned_ecos & reviewed) / len(reviewed), 1)
        if reviewed else 0.0,
        returned=len(returned_ecos), reviewed=len(reviewed), by_unit=by_unit,
        stage_unit={s: {u: median(stage_unit_days[(s, u)]) for u in units} for s in STAGES},
        stage_median=stage_median, stage_p80=stage_p80,
        returns_by_stage={s: returns_by_stage[s] for s in STAGES if returns_by_stage[s]},
        bottlenecks=bottlenecks, warnings=warnings, trend=trend)


# ---------- return comments for the AI ----------

def returns_window(events: list[EcoEvent], ecos: list[Eco], as_of: datetime,
                   months: int = 6) -> list[EcoEvent]:
    """Return events in the last few months before the export date."""
    start = datetime(as_of.year - (1 if as_of.month <= months else 0),
                     (as_of.month - months - 1) % 12 + 1, 1, tzinfo=UTC)
    return sorted((v for v in events if v.outcome == "returned" and v.left
                   and v.left >= start), key=lambda v: (v.left, v.eco))


def returns_input(returns: list[EcoEvent], ecos: list[Eco]) -> str:
    unit = {e.eco: (e.business_unit, e.change_type) for e in ecos}
    return "\n".join(f"{v.eco} | {v.stage} | {unit[v.eco][0]} | {unit[v.eco][1]} | "
                     f"{v.comment}" for v in returns)


# ---------- storage ----------

ECO_FIELDS = ["eco", "title", "business_unit", "change_type", "priority", "originator",
              "affected_parts", "created", "status", "released"]
EVENT_FIELDS = ["eco", "seq", "stage", "actor", "entered", "left", "outcome", "comment"]


def as_utc(dt: datetime) -> datetime:
    """Export timestamps without a zone are taken as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _dt(value: str, what: str) -> datetime | None:
    if not value:
        return None
    try:
        return as_utc(datetime.fromisoformat(value))
    except ValueError as e:
        raise QualityError(f"{what}: '{value}' is not a date and time like "
                           "2026-03-04T09:30.") from e


class EcoStore:
    def __init__(self, engine: Engine):
        self.engine = engine
        self._cache: tuple[int, EcoAnalytics] | None = None

    def seed_if_empty(self, dataset_dir: Path, actor: str) -> None:
        if self.dataset() is not None or not (dataset_dir / "ecos.csv").exists():
            return
        meta = json.loads((dataset_dir / "eco_planted.json").read_text())
        self.load((dataset_dir / "ecos.csv").read_text(),
                  (dataset_dir / "eco_events.csv").read_text(),
                  "Fictional ECO history (demo)", as_utc(datetime.fromisoformat(meta["as_of"])),
                  actor)

    def load(self, ecos_csv: str, events_csv: str, name: str, as_of: datetime | None,
             actor: str) -> EcoDataset:
        eco_rows = read_csv(ecos_csv, ECO_FIELDS[:4] + ["created", "status"], "ECO")
        event_rows = read_csv(events_csv, ["eco", "seq", "stage", "actor", "entered",
                                           "outcome"], "ECO events")
        ecos = []
        for r in eco_rows:
            ecos.append(Eco(dataset_id=0, eco=r["eco"], title=r.get("title", ""),
                            business_unit=r["business_unit"], change_type=r["change_type"],
                            priority=r.get("priority", "normal"),
                            originator=r.get("originator", ""),
                            affected_parts=int(r.get("affected_parts") or 0),
                            created=_dt(r["created"], r["eco"]), status=r["status"],
                            released=_dt(r.get("released", ""), r["eco"])))
        known = {e.eco for e in ecos}
        events = []
        for r in event_rows:
            if r["eco"] not in known:
                raise QualityError(f"ECO events mention {r['eco']}, which is not in the ECO "
                                   "file.")
            events.append(EcoEvent(dataset_id=0, eco=r["eco"], seq=int(r["seq"]),
                                   stage=r["stage"], actor=r["actor"],
                                   entered=_dt(r["entered"], r["eco"]),
                                   left=_dt(r.get("left", ""), r["eco"]),
                                   outcome=r["outcome"], comment=r.get("comment", "")))
        as_of = as_of or max([e.left or e.entered for e in events] + [e.created for e in ecos])
        with Session(self.engine) as s:
            ds = EcoDataset(name=name, as_of=as_of, ecos=len(ecos), events=len(events),
                            loaded_by=actor)
            s.add(ds)
            s.flush()
            for x in ecos + events:
                x.dataset_id = ds.id
            s.add_all(ecos + events)
            audit(s, actor, "dataset_loaded",
                  f"{name}: {len(ecos)} ECOs, {len(events)} stage events, as of {as_of:%Y-%m-%d}")
            s.commit()
            s.refresh(ds)
        return ds

    def dataset(self) -> EcoDataset | None:
        with Session(self.engine) as s:
            return s.exec(select(EcoDataset).order_by(EcoDataset.id.desc())).first()

    def datasets(self) -> list[EcoDataset]:
        with Session(self.engine) as s:
            return list(s.exec(select(EcoDataset).order_by(EcoDataset.id)))

    def rows(self) -> tuple[list[Eco], list[EcoEvent]]:
        ds = self.dataset()
        if ds is None:
            return [], []
        with Session(self.engine) as s:
            return (list(s.exec(select(Eco).where(Eco.dataset_id == ds.id))),
                    list(s.exec(select(EcoEvent).where(EcoEvent.dataset_id == ds.id))))

    def analytics(self) -> EcoAnalytics | None:
        ds = self.dataset()
        if ds is None:
            return None
        if self._cache and self._cache[0] == ds.id:
            return self._cache[1]
        ecos, events = self.rows()
        result = analyze(ecos, events, ds.as_of)
        self._cache = (ds.id, result)
        return result

    def eco(self, key: str) -> tuple[Eco | None, list[EcoEvent]]:
        ds = self.dataset()
        if ds is None:
            return None, []
        with Session(self.engine) as s:
            eco = s.exec(select(Eco).where(Eco.dataset_id == ds.id, Eco.eco == key)).first()
            events = list(s.exec(select(EcoEvent).where(EcoEvent.dataset_id == ds.id,
                                                        EcoEvent.eco == key)
                                 .order_by(EcoEvent.seq)))
        return eco, events

    def search(self, q: str, limit: int = 30) -> list[Eco]:
        ds = self.dataset()
        if ds is None or not q.strip():
            return []
        words = q.lower().split()
        ecos, _ = self.rows()
        hits = [e for e in ecos if all(w in f"{e.eco} {e.title} {e.originator} "
                                       f"{e.business_unit}".lower() for w in words)]
        return sorted(hits, key=lambda e: e.created, reverse=True)[:limit]


def to_csv(rows: list[dict], fields: list[str]) -> str:
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return out.getvalue()
