from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from plm_insight.app import create_app
from plm_insight.config import load_settings
from plm_insight.db import make_engine
from plm_insight.quality import QualityStore


@pytest.fixture
def settings(tmp_path):
    return replace(load_settings(), db_path=str(tmp_path / "t.db"))


@pytest.fixture
def engine(settings):
    return make_engine(settings.db_path)


@pytest.fixture
def client(settings, engine):
    return TestClient(create_app(settings, engine=engine))


def first(store, rule, status="open"):
    return store.issues(rule=rule, status=status)[0]


def test_pages_render(client, engine):
    store = QualityStore(engine)
    issue = first(store, "DQ-DUP")
    for path in ["/", "/issues", "/issues?queue=compliance", f"/issues/{issue.id}",
                 f"/parts/{issue.part_number}", "/changes", "/data", "/audit"]:
        assert client.get(path).status_code == 200, path
    page = client.get("/").text
    assert "Quality score" in page and "Rule check on the demo data" in page
    assert client.get("/parts/999-999999").status_code == 404


def clean_duplicates(store):
    return [i for i in store.issues(rule="DQ-DUP") if not i.fix_data["classification_conflict"]]


def test_merge_duplicates_builds_change_rows(client, engine):
    store = QualityStore(engine)
    issue = clean_duplicates(store)[0]
    keep = next(p for p in issue.part_list if p != issue.fix_data["survivor"])
    refused = client.post(f"/issues/{issue.id}/decide",
                          data={"decision": "accepted", "value": keep})
    assert "The rule proposes" in refused.text  # differing from the proposal needs a reason
    response = client.post(f"/issues/{issue.id}/decide",
                           data={"decision": "accepted", "value": keep,
                                 "change_reason": "Has the current drawing revision"},
                           follow_redirects=True)
    assert response.status_code == 200 and "accepted" in response.text
    rows = store.change_list()
    retired = [p for p in issue.part_list if p != keep]
    assert any(r["action"] == "set lifecycle" and r["object"] == retired[0] for r in rows)
    assert all(r["new"].endswith(f"{keep})") or r["new"] == keep for r in rows)
    csv_text = client.get("/changes.csv").text
    assert csv_text.startswith("issue,rule,object,action,old,new") and issue.key in csv_text
    bad = clean_duplicates(store)[1]
    assert "must be one of the duplicates" in client.post(
        f"/issues/{bad.id}/decide", data={"decision": "accepted", "value": "100-000000"}).text


def test_reject_needs_reason_and_carries_over_to_the_next_import(client, engine, settings):
    store = QualityStore(engine)
    issue = first(store, "DQ-UOM")
    assert "Say why" in client.post(f"/issues/{issue.id}/decide",
                                    data={"decision": "rejected"}).text
    client.post(f"/issues/{issue.id}/decide",
                data={"decision": "rejected", "reason": "PK is right for this kit"})
    accepted = first(store, "DQ-REL")
    client.post(f"/issues/{accepted.id}/decide", data={"decision": "accepted"})
    d = settings.dataset_dir
    files = {"parts": ("parts.csv", (d / "parts.csv").read_bytes(), "text/csv"),
             "bom": ("bom.csv", (d / "bom.csv").read_bytes(), "text/csv")}
    response = client.post("/data", files=files, data={"name": "Second export",
                                                       "controlled": "Aerospace Systems"})
    assert response.status_code == 200
    again = next(i for i in store.issues(rule="DQ-UOM", status="")
                 if i.fingerprint == issue.fingerprint)
    assert again.status == "rejected" and "carried over" in again.note
    reopened = next(i for i in store.issues(rule="DQ-REL", status="")
                    if i.fingerprint == accepted.fingerprint)
    assert reopened.status == "open" and "still in this export" in reopened.note
    assert "Second export" in client.get("/").text


def test_only_trade_compliance_decides_export_classification(client, engine):
    store = QualityStore(engine)
    issue = first(store, "DQ-EXP")
    page = client.get(f"/issues/{issue.id}").text
    assert "Only Trade Compliance can decide this" in page
    denied = client.post(f"/issues/{issue.id}/decide",
                         data={"decision": "accepted", "value": "EAR99"})
    assert "Only Trade Compliance" in denied.text and denied.status_code == 403
    assert "access_denied" in client.get("/audit").text  # refused attempts are audited
    client.cookies.set("pi_user", "tc")
    assert "Enter the export classification" in client.post(
        f"/issues/{issue.id}/decide", data={"decision": "accepted"}).text
    client.post(f"/issues/{issue.id}/decide", data={"decision": "accepted", "value": "9a991"})
    row = next(r for r in store.change_list() if r["issue"] == issue.key)
    assert row["action"] == "set export_class" and row["new"] == "9A991"


def test_engineers_cannot_decide_or_import(client, engine):
    store = QualityStore(engine)
    client.cookies.set("pi_user", "eng")
    issue = first(store, "DQ-ATTR")
    assert client.post(f"/issues/{issue.id}/decide",
                       data={"decision": "accepted", "value": "x"}).status_code == 403
    assert client.get("/audit").status_code == 403
    assert client.get("/").status_code == 200  # the scorecard is for everyone
    client.cookies.set("pi_user", "steward")
    assert "access_denied" in client.get("/audit").text


def test_import_validation(client):
    files = {"parts": ("p.csv", b"part_number,description\n1,x\n", "text/csv"),
             "bom": ("b.csv", b"parent,find_number,child,quantity,uom\n", "text/csv")}
    assert "missing columns" in client.post("/data", files=files).text


def test_security_headers(client):
    headers = client.get("/static/css/app.css").headers
    assert headers["x-frame-options"] == "DENY" and headers["referrer-policy"] == "same-origin"


def test_merging_parts_with_different_classifications_needs_trade_compliance(client, engine):
    store = QualityStore(engine)
    issue = next(i for i in store.issues(rule="DQ-DUP") if i.fix_data["classification_conflict"])
    assert "only Trade Compliance can approve this merge" in client.get(f"/issues/{issue.id}").text
    page = client.post(f"/issues/{issue.id}/decide", data={"decision": "accepted"}).text
    assert "Trade Compliance must approve" in page
    client.cookies.set("pi_user", "tc")
    client.post(f"/issues/{issue.id}/decide", data={"decision": "accepted"})
    assert store.issue(issue.id).status == "accepted"


SUITE = ("Backlog Pilot=http://bp, Leadership Pilot=http://lp, PLM Insight=http://pi, "
         "Future App=http://sp")


def test_launcher_menu_renders(settings, engine, monkeypatch):
    monkeypatch.setenv("MISSION_CONTROL_APPS", SUITE)
    page = TestClient(create_app(settings, engine=engine)).get("/").text
    assert 'class="suite-menu"' in page and 'href="http://sp"' in page
    assert 'aria-current=page' in page
