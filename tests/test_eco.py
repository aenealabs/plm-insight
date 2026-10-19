import json
import re
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from plm_insight.app import create_app
from plm_insight.config import load_settings
from plm_insight.db import make_engine
from plm_insight.eco import Eco, EcoEvent, EcoStore, analyze, returns_input, returns_window
from plm_insight.llm.replay import ReplayProvider
from plm_insight.models import Quote, ReturnAnalysis, ReturnTheme
from plm_insight.record_demos import demo_inputs
from plm_insight.suite import SuiteClient, SuiteSettings

AS_OF = datetime(2026, 10, 1, tzinfo=UTC)


def d(day: int, month: int = 9) -> datetime:
    return datetime(2026, month, day, tzinfo=UTC)


def test_analytics_on_a_small_history():
    ecos = [Eco(dataset_id=1, eco=f"E{i}", title="t", business_unit="EP", change_type="Design",
                priority="normal", originator="o", affected_parts=1, created=d(1),
                status="Released", released=d(1 + 10 * (i + 1) // 5)) for i in range(5)]
    ecos.append(Eco(dataset_id=1, eco="OPEN", title="Late one", business_unit="EP",
                    change_type="Design", priority="normal", originator="o", affected_parts=1,
                    created=d(2), status="Change Board"))
    events = []
    for i, e in enumerate(ecos[:5]):
        events.append(EcoEvent(dataset_id=1, eco=e.eco, seq=1, stage="Change Board",
                               actor="Slow" if i < 1 else f"A{i}", entered=d(1),
                               left=d(2 + (8 if i < 1 else 0)), outcome="approved"))
    events.append(EcoEvent(dataset_id=1, eco="E1", seq=2, stage="Technical Review", actor="R",
                           entered=d(3), left=d(4), outcome="returned", comment="No cost impact."))
    events.append(EcoEvent(dataset_id=1, eco="OPEN", seq=1, stage="Change Board", actor="A1",
                           entered=d(2), outcome="in progress"))
    a = analyze(ecos, events, AS_OF)
    assert a.released == 5 and len(a.open) == 1
    assert a.warnings[0]["eco"] == "OPEN" and a.warnings[0]["rule"] == "ECO-SLOW-STAGE"
    assert a.return_rate == 100.0  # only E1 was reviewed, and it came back
    assert returns_input(returns_window(events, ecos, AS_OF), ecos).startswith("E1 | Technical")


def test_demo_history_shows_the_planted_patterns():
    settings = load_settings()
    store = EcoStore(make_engine(":memory:"))
    store.seed_if_empty(settings.dataset_dir, "seed")
    a = store.analytics()
    planted = json.loads((settings.dataset_dir / "eco_planted.json").read_text())
    assert [b["actor"] for b in a.bottlenecks] == [planted["bottleneck_approver"]]
    slowest = max(a.by_unit, key=lambda u: u["median"])["unit"]
    assert slowest == planted["slowest_unit"]
    assert 25 <= a.return_rate <= 36  # "about a third bounce back"
    assert 14 <= a.cycle_median <= 24  # "about three weeks"


class Themes:
    """A return analysis that cites one wrong ECO, one bad quote, and leaves one out."""

    name = "stub"

    def return_themes(self, input_text):
        rows = [line.split(" | ") for line in input_text.splitlines()]
        ids = [r[0] for r in rows]
        return ReturnAnalysis(headline="Missing information at submit.", themes=[
            ReturnTheme(name="Everything else", description="d", eco_ids=ids[1:] + ["ECO-99-99999"],
                        quotes=[Quote(eco=rows[1][0], quote=rows[1][4][:20]),
                                Quote(eco=rows[2][0], quote="words nobody wrote")],
                        prevention="Require affected parts and cost impact at submit.")])


@pytest.fixture
def client(tmp_path):
    settings = replace(load_settings(), db_path=str(tmp_path / "t.db"))
    sent = []
    suite = SuiteSettings(backlog_url="http://bp", token="t", public_url="http://pi")
    transport = httpx.MockTransport(lambda r: sent.append(json.loads(r.content))
                                    or httpx.Response(201, json={"id": 9}))
    c = TestClient(create_app(settings, engine=make_engine(settings.db_path), provider=Themes(),
                              suite=suite, suite_client=SuiteClient(suite, transport)))
    c.sent = sent
    return c


def test_eco_pages_and_search(client):
    page = client.get("/ecos").text
    assert "Median cycle time" in page and "M. Okonkwo" in page and "Slow ECOs" in page
    assert re.search(r"[A-Z][a-z]{2} \d{4} to [A-Z][a-z]{2} \d{4}, \d+ returns on \d+ ECOs",
                     page)  # the window keeps its case (not "sep 2026 ... ecos")
    found = client.get("/ecos?q=busbar kit").text
    assert "Results for" in found and "/ecos/ECO-" in found
    eco = found.split('href="/ecos/')[1].split('"')[0]
    assert "Where it has been" in client.get(f"/ecos/{eco}").text
    assert client.get("/ecos/ECO-00-00000").status_code == 404


def test_return_themes_are_checked_and_counted_by_code(client):
    _, text = demo_inputs(load_settings(), "returns")[0]
    ids = [line.split(" | ")[0] for line in text.splitlines()]
    expected = len(ids) - 1  # every return but the first, each cited once
    page = client.post("/ecos/why", follow_redirects=True).text
    assert "2 flagged" in page
    assert "were not returned in this period: ECO-99-99999" in page
    assert "quote not found" in page
    assert "not placed in any theme" in page
    assert f"{expected} <span" in page  # counted from verified ids, not from the model
    assert f"returns on {len(set(ids))} ECOs" in page
    page = client.post("/ecos/themes/0/send").text
    assert "Sent &#39;Everything else&#39; to Backlog Pilot" in page
    assert client.sent[0]["title"] == "Stop ECO returns: Everything else"
    assert "Require affected parts" in client.sent[0]["text"]
    client.cookies.set("pi_user", "eng")
    assert client.post("/ecos/why").status_code == 403


def test_offline_demo_has_the_return_themes_recorded():
    settings = load_settings()
    _, text = demo_inputs(settings, "returns")[0]
    assert ReplayProvider(settings.replay_dir).return_themes(text).themes
