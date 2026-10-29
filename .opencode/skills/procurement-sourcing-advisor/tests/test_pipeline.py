from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from conftest import FIXTURES, ROOT
from run_pipeline import run_pipeline


def test_pipeline_produces_three_artifacts_and_valid_report(tmp_path: Path):
    manifest = run_pipeline(FIXTURES, output_dir=tmp_path, report_id="test-report")
    assert Path(manifest["outputs"]["report_md"]).exists()
    assert Path(manifest["outputs"]["candidates_csv"]).exists()
    assert Path(manifest["outputs"]["report_json"]).exists()

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    schema = json.loads((ROOT / "schemas" / "procurement_report.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(report, schema)

    assert report["summary"]["recommendation"]["product_id"] == "100001"
    assert report["summary"]["alternatives"]
    assert "pdd" in report["scope"]["excluded_platforms"]
    assert "拼多多" in report["scope"]["price_note"]

    by_id = {item["product_id"]: item for item in report["candidates"]}
    assert by_id["700002"]["price"] is None
    assert by_id["700002"]["price_status"] == "hidden"
    assert by_id["100001-32"]["eligible"] is False

    assert report["variant_matrix"]
    matrix = next(item for item in report["variant_matrix"] if item["listing_id"] == "100001")
    assert matrix["variant_count"] == 3
    assert "ram_gb" in matrix["differing_attributes"]

    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "拼多多未覆盖" in markdown
    assert "同链接配置对比" in markdown
    assert "评价摘要" in markdown
    assert "需求匹配矩阵" in markdown
    assert "性价比与帕累托前沿" in markdown
    assert "评分字段取值" in markdown
    assert "确认记录" in markdown
    assert "UNKNOWN" in markdown or "缺失" in markdown
    assert "备选方案" in markdown

    assert report["fit_summary"]["passed"] >= 1
    assert report["value_summary"]["pool_size"] >= 1
    assert report["summary"]["recommendation"]["fit_score"] >= 0.8

    scored = json.loads((tmp_path / "scored.json").read_text(encoding="utf-8"))
    candidate_schema = json.loads((ROOT / "schemas" / "candidate.schema.json").read_text(encoding="utf-8"))
    for candidate in scored["candidates"]:
        jsonschema.validate(candidate, candidate_schema)


def test_invalid_capture_stops_the_pipeline(tmp_path: Path):
    brief = json.loads((FIXTURES / "brief.json").read_text(encoding="utf-8"))
    scorecard = json.loads((FIXTURES / "scorecard.json").read_text(encoding="utf-8"))
    capture = json.loads((FIXTURES / "raw_capture.json").read_text(encoding="utf-8"))
    capture["captures"][0]["variants"][0]["option_path"] = []
    (tmp_path / "brief.json").write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "scorecard.json").write_text(json.dumps(scorecard, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "raw_capture.json").write_text(json.dumps(capture, ensure_ascii=False), encoding="utf-8")

    try:
        run_pipeline(tmp_path, output_dir=tmp_path / "out")
    except ValueError as exc:
        assert "未通过校验" in str(exc)
    else:  # pragma: no cover - the test fails loudly if validation is skipped
        raise AssertionError("pipeline accepted an invalid capture")


def test_unconfirmed_plan_is_rejected(tmp_path: Path):
    brief = json.loads((FIXTURES / "brief.json").read_text(encoding="utf-8"))
    scorecard = json.loads((FIXTURES / "scorecard.json").read_text(encoding="utf-8"))
    capture = json.loads((FIXTURES / "raw_capture.json").read_text(encoding="utf-8"))
    brief.pop("confirmed", None)
    (tmp_path / "brief.json").write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "scorecard.json").write_text(json.dumps(scorecard, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "raw_capture.json").write_text(json.dumps(capture, ensure_ascii=False), encoding="utf-8")

    try:
        run_pipeline(tmp_path, output_dir=tmp_path / "out")
    except ValueError as exc:
        assert "尚未确认" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("pipeline accepted an unconfirmed plan")

    manifest = run_pipeline(tmp_path, output_dir=tmp_path / "out2", require_confirmation=False)
    assert manifest["outputs"]["report_md"]


def test_brief_and_scorecard_validate_against_schemas():
    brief = json.loads((FIXTURES / "brief.json").read_text(encoding="utf-8"))
    brief_schema = json.loads((ROOT / "schemas" / "procurement_brief.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(brief, brief_schema)

    scorecard = json.loads((FIXTURES / "scorecard.json").read_text(encoding="utf-8"))
    scorecard_schema = json.loads((ROOT / "schemas" / "scorecard.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(scorecard, scorecard_schema)
