from __future__ import annotations

import json
from pathlib import Path

from conftest import FIXTURES
from normalize_candidates import normalize_capture
from run_pipeline import run_pipeline
from validate_capture import validate_capture


def _payload() -> dict:
    return json.loads((FIXTURES / "raw_capture.json").read_text(encoding="utf-8"))


def _official(**overrides) -> dict:
    payload = {
        "manufacturer": "Lenovo",
        "model": "ThinkPad T14 Gen 5 21ML",
        "region": "CN",
        "url": "https://www.lenovo.com.cn/thinkpad/t14-gen5",
        "captured_at": "2026-10-30T10:00:00+08:00",
        "match_status": "exact",
        "matched_text": "ThinkPad T14 Gen 5 (21ML) 产品规格",
        "specs": {
            "重量": "1.31kg",
            "接口": "Thunderbolt 4 ×2 / USB-A ×2",
            "CPU": "Intel Core Ultra 5 125U",
        },
        "evidence": [
            {
                "field": "official.specs",
                "snippet": "重量 1.31kg；Thunderbolt 4 ×2 / USB-A ×2",
                "source_url": "https://www.lenovo.com.cn/thinkpad/t14-gen5",
                "captured_at": "2026-10-30T10:00:00+08:00",
            }
        ],
    }
    payload.update(overrides)
    return payload


def _normalized(payload: dict) -> dict:
    return normalize_capture(payload, fetched_at="2026-10-30T00:00:00+00:00")


def test_exact_official_specs_fill_missing_attributes():
    payload = _payload()
    payload["captures"][0]["official"] = _official()
    assert validate_capture(payload)["ok"]

    result = _normalized(payload)
    base = next(item for item in result["candidates"] if item["variant_id"] == "100001")

    assert base["attributes"]["weight_kg"] == 1.31
    assert base["attributes"]["ports"] == "USB-C / HDMI"  # platform value wins
    assert base["official"]["match_status"] == "exact"
    assert "weight_kg" in base["official"]["filled_attributes"]
    assert any(source["type"] == "official" for source in base["sources"])
    assert any(evidence["source"] == "official" for evidence in base["evidence"])
    assert result["summary"]["with_official_specs"] == 3


def test_official_conflict_keeps_platform_value_and_is_recorded():
    payload = _payload()
    payload["captures"][0]["official"] = _official()

    result = _normalized(payload)
    upgraded = next(item for item in result["candidates"] if item["variant_id"] == "100001-32")

    assert upgraded["attributes"]["cpu"] == "Intel Core Ultra 7 155U"
    conflicts = {item["field"]: item for item in upgraded["official"]["conflicts"]}
    assert conflicts["cpu"]["official"] == "Intel Core Ultra 5 125U"
    assert upgraded["needs_review"] is True


def test_family_match_is_recorded_but_never_used_for_scoring():
    payload = _payload()
    payload["captures"][0]["official"] = _official(
        match_status="family",
        matched_text="ThinkPad T14 系列",
        specs={"重量": "9kg"},
    )
    validation = validate_capture(payload)
    assert validation["ok"]

    result = _normalized(payload)
    base = next(item for item in result["candidates"] if item["variant_id"] == "100001")
    assert "weight_kg" not in base["attributes"]
    assert base["official"]["match_status"] == "family"
    assert base["official"]["filled_attributes"] == []


def test_variant_official_takes_precedence_over_capture_official():
    payload = _payload()
    capture = payload["captures"][0]
    capture["official"] = _official(specs={"重量": "2kg"})
    capture["variants"][0]["official"] = _official(specs={"重量": "1.31kg"})

    result = _normalized(payload)
    base = next(item for item in result["candidates"] if item["variant_id"] == "100001")
    sibling = next(item for item in result["candidates"] if item["variant_id"] == "100001-24")
    assert base["attributes"]["weight_kg"] == 1.31
    assert sibling["attributes"]["weight_kg"] == 2.0


def test_exact_official_match_without_snippet_is_rejected():
    payload = _payload()
    official = _official()
    official["evidence"] = [
        {"field": "official.specs", "snippet": "", "source_url": official["url"]}
    ]
    payload["captures"][0]["official"] = official

    result = validate_capture(payload)
    assert not result["ok"]
    assert any("snippet" in error for error in result["errors"])


def test_official_block_cannot_smuggle_price_or_stock():
    payload = _payload()
    payload["captures"][0]["official"] = _official(specs={"重量": "1.31kg", "价格": "9999"})

    result = validate_capture(payload)
    assert not result["ok"]
    assert any("价格" in error for error in result["errors"])


def test_official_evidence_cannot_be_price_evidence():
    payload = _payload()
    official = _official()
    official["evidence"] = [
        {
            "field": "price",
            "snippet": "官网建议零售价 9999 元",
            "source_url": official["url"],
        }
    ]
    payload["captures"][0]["official"] = official

    result = validate_capture(payload)
    assert not result["ok"]
    assert any("价格" in error for error in result["errors"])


def test_report_exposes_official_source_and_keeps_price_separate(tmp_path: Path):
    brief = json.loads((FIXTURES / "brief.json").read_text(encoding="utf-8"))
    scorecard = json.loads((FIXTURES / "scorecard.json").read_text(encoding="utf-8"))
    capture = _payload()
    capture["captures"][0]["official"] = _official()

    (tmp_path / "brief.json").write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "scorecard.json").write_text(json.dumps(scorecard, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "raw_capture.json").write_text(json.dumps(capture, ensure_ascii=False), encoding="utf-8")

    manifest = run_pipeline(tmp_path, output_dir=tmp_path / "out")
    assert manifest["summary"]["normalized"]["with_official_specs"] == 3

    markdown = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "官网参数核对" in markdown
    assert "ThinkPad T14 Gen 5 21ML" in markdown
    assert "https://www.lenovo.com.cn/thinkpad/t14-gen5" in markdown
    assert "官网：仅对存在明确型号的候选核对厂商官网参数" in markdown
    assert "价格仅来自淘宝/京东" in markdown

    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    candidate = next(item for item in report["candidates"] if item["variant_id"] == "100001")
    assert candidate["official"]["match_status"] == "exact"
    assert candidate["price"] == 5499
    assert "price" not in (candidate["official"].get("specs") or {})
