import csv
import json

from plm_insight.config import load_settings
from plm_insight.rules import analyze, detection, material_from, normalize

DEMO = load_settings().dataset_dir


def part(pn, description, **kw):
    base = {"part_number": pn, "description": description, "part_type": "Fastener",
            "business_unit": "Electrical Products", "material": "STAINLESS STEEL 316",
            "size": "M8X20", "uom": "EA", "weight_kg": "0.01", "make_buy": "buy",
            "lifecycle": "Released", "export_class": "EAR99", "superseded_by": "",
            "created": "2020-01-01", "created_by": "x"}
    return base | kw


def test_normalize_ignores_case_order_punctuation_and_abbreviations():
    assert normalize("HEX BOLT M8X20 SS316") == normalize("Bolt, Hex, m8 x 20, Stainless Steel 316")
    assert normalize("HEX NUT M8") != normalize("FLAT WASHER M8")
    assert material_from("Hex bolt m8x20 stainless steel 316",
                         {"STAINLESS STEEL 316", "STAINLESS STEEL 304"}) == "STAINLESS STEEL 316"


def test_duplicates_need_same_kind_and_material():
    parts = [part("100-000001", "HEX BOLT M8X20 SS316"),
             part("100-000002", "Bolt, Hex, m8 x 20, Stainless Steel 316",
                  business_unit="eMobility"),
             part("100-000003", "SOCKET HEAD CAP SCREW M8X20 SS316"),  # different kind
             part("100-000004", "HEX BOLT M8X20 BRS", material="BRASS"),  # different material
             part("100-000005", "Hex bolt m8x20 stainless steel 316", material="")]  # missing
    bom = [{"parent": "A", "find_number": "10", "child": "100-000002", "quantity": "1",
            "uom": "EA"}]
    dups = [f for f in analyze(parts, bom, set()) if f.rule == "DQ-DUP"]
    assert len(dups) == 1
    assert dups[0].parts == ["100-000001", "100-000002", "100-000005"]
    assert dups[0].fix["survivor"] == "100-000002"  # the one used in a BOM
    assert dups[0].evidence["units"] == ["Electrical Products", "eMobility"]


def test_bom_and_attribute_rules():
    parts = [part("200-000001", "PANEL ASSY", part_type="Assembly", material="", size="",
                  business_unit="Aerospace Systems", export_class="9A991"),
             part("200-000002", "BRACKET A", lifecycle="In Work", size="1", export_class=""),
             part("200-000003", "BRACKET B", lifecycle="Obsolete", superseded_by="200-000004",
                  size="2"),
             part("200-000004", "BRACKET C", size="3", weight_kg="")]
    bom = [{"parent": "200-000001", "find_number": "10", "child": "200-000002",
            "quantity": "1", "uom": "EA"},
           {"parent": "200-000001", "find_number": "20", "child": "200-000003",
            "quantity": "1", "uom": "EA"},
           {"parent": "200-000001", "find_number": "30", "child": "200-999999",
            "quantity": "1", "uom": "EA"},
           {"parent": "200-000001", "find_number": "40", "child": "200-000004",
            "quantity": "1", "uom": "PK"}]
    found = {f.rule: f for f in analyze(parts, bom, {"Aerospace Systems"})}
    assert found["DQ-REL"].fix == {"action": "release_child", "part": "200-000002"}
    assert found["DQ-OBS"].fix["new"] == "200-000004"
    assert found["DQ-ORPHAN"].parts == ["200-000001", "200-999999"]
    assert found["DQ-UOM"].fix["lines"] == [["200-000001", "40"]]
    assert found["DQ-ATTR"].fix["fields"] == ["weight_kg"]
    exp = found["DQ-EXP"]
    assert exp.part_number == "200-000002" and exp.fix == {}  # never proposes a class
    assert "controlled assembly" in exp.evidence["reason"]


def test_rules_find_every_planted_problem_in_the_demo_export():
    parts = list(csv.DictReader((DEMO / "parts.csv").open()))
    bom = list(csv.DictReader((DEMO / "bom.csv").open()))
    planted = json.loads((DEMO / "planted.json").read_text())
    result = detection(analyze(parts, bom, {"Aerospace Systems"}), planted)
    for rule, d in result.items():
        assert d["found"] == d["planted"] == d["flagged"], (rule, d)


def test_duplicates_with_different_classifications_are_flagged():
    parts = [part("100-000001", "HEX BOLT M8X20 SS316", export_class="9A610.x"),
             part("100-000002", "Bolt, Hex, m8 x 20, Stainless Steel 316")]
    dup = next(f for f in analyze(parts, [], set()) if f.rule == "DQ-DUP")
    assert dup.fix["classification_conflict"] is True
    assert dup.evidence["export_classes"] == ["9A610.x", "EAR99"]
