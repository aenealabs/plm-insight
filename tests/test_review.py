import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from plm_insight.app import create_app
from plm_insight.config import load_settings
from plm_insight.db import make_engine
from plm_insight.llm.replay import ReplayProvider
from plm_insight.models import CitedValue, CompliancePacket, DuplicateReview, Point
from plm_insight.quality import QualityStore
from plm_insight.record_demos import demo_inputs
from plm_insight.review import check_points, remove_classifications
from plm_insight.suite import SuiteClient, SuiteSettings


class StubAI:
    """Cites real values, plus one wrong value, and writes a classification it must not."""

    name = "stub"

    def review_duplicates(self, input_text):
        parts = json.loads(input_text)["parts"]
        a, b = parts[0], parts[1]
        return DuplicateReview(
            verdict="same part", keep=a["part_number"],
            reasons=[Point(text="Same size.", cites=[
                CitedValue(part_number=a["part_number"], field="size", value=a["size"]),
                CitedValue(part_number=b["part_number"], field="size", value=b["size"])]),
                Point(text="Heavier.", cites=[
                    CitedValue(part_number=b["part_number"], field="weight_kg", value="9.999")])],
            before_merging=["Confirm the supplier drawing."])

    def compliance_packet(self, input_text):
        part = json.loads(input_text)["part"]
        return CompliancePacket(
            summary="A machined part; likely 9A991 like its assembly.",
            evidence=[Point(text="Released part.", cites=[
                CitedValue(part_number=part["part_number"], field="lifecycle",
                           value=part["lifecycle"])])],
            questions=["Was it specially designed? Category XI may apply."])


@pytest.fixture
def backlog():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(201, json={"id": 5, "status": "new"})
    return sent, handler


@pytest.fixture
def client(tmp_path, backlog):
    settings = replace(load_settings(), db_path=str(tmp_path / "t.db"))
    suite = SuiteSettings(backlog_url="http://backlog", token="secret", public_url="http://pi")
    app = create_app(settings, engine=make_engine(settings.db_path), provider=StubAI(),
                     suite=suite, suite_client=SuiteClient(suite, httpx.MockTransport(backlog[1])))
    c = TestClient(app)
    c.store = QualityStore(app.state.engine)
    return c


def test_check_points():
    values = {"100-000001": {"size": "M8X20", "material": ""}}
    ok, wrong, unknown, empty = check_points([
        Point(text="a", cites=[CitedValue(part_number="100-000001", field="size",
                                          value="m8x20")]),
        Point(text="b", cites=[CitedValue(part_number="100-000001", field="size",
                                          value="M8X25")]),
        Point(text="c", cites=[CitedValue(part_number="999-000000", field="size", value="x")]),
        Point(text="d", cites=[])], values)
    assert ok.problems == []
    assert wrong.problems == ["100-000001 size is M8X20, not M8X25."]
    assert "not part of this finding" in unknown.problems[0]
    assert empty.problems == ["Cites no values."]


def test_remove_classifications():
    packet, n = remove_classifications(CompliancePacket(
        summary="Maybe 9A610.x, or EAR99.", evidence=[],
        questions=["ITAR Category XI?", "USML XI(c) covers it", "Size 3A?"]))
    assert n == 4
    assert "9A610" not in packet.summary and "EAR99" not in packet.summary
    assert packet.questions[2] == "Size 3A?"


def test_duplicate_review_is_checked(client):
    issue = client.store.issues(rule="DQ-DUP")[0]
    page = client.post(f"/issues/{issue.id}/ai", follow_redirects=True).text
    assert "same part" in page and "1 flagged" in page
    assert "weight_kg is" in page and "not 9.999" in page
    assert "Confirm the supplier drawing." in page
    assert "issue_ai_review" in client.get("/audit").text


def test_compliance_packet_never_shows_a_classification(client):
    issue = client.store.issues(rule="DQ-EXP")[0]
    assert client.post(f"/issues/{issue.id}/ai").status_code == 403  # steward, not compliance
    client.cookies.set("pi_user", "tc")
    page = client.post(f"/issues/{issue.id}/ai", follow_redirects=True).text
    assert "[classification removed]" in page and "likely 9A991" not in page
    assert "Removed 2 classification-like strings" in page


def test_prevent_sends_to_backlog_pilot(client, backlog):
    page = client.post("/rules/DQ-DUP/send").text
    assert "Sent DQ-DUP to Backlog Pilot" in page
    item = backlog[0][0]
    assert item["title"] == "Prevent: duplicate parts in PLM"
    assert "130 cases" in item["text"] and item["source_url"] == "http://pi/?rule=DQ-DUP"
    client.cookies.set("pi_user", "eng")
    assert client.post("/rules/DQ-DUP/send").status_code == 403


def test_offline_demo_has_recordings_for_the_demo_items():
    settings = load_settings()
    replay = ReplayProvider(settings.replay_dir)
    for kind in ("duplicates", "compliance"):
        for key, text in demo_inputs(settings, kind):
            out = (replay.review_duplicates(text) if kind == "duplicates"
                   else replay.compliance_packet(text))
            assert out is not None, key
