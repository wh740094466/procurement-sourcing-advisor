from __future__ import annotations

import json
from pathlib import Path

from common import UNKNOWN
from conftest import FIXTURES
from normalize_candidates import normalize_capture, normalize_payload
from validate_capture import validate_capture


def _capture() -> dict:
    return json.loads((FIXTURES / "raw_capture.json").read_text(encoding="utf-8"))


def _normalized() -> dict:
    return normalize_capture(_capture(), fetched_at="2026-10-29T00:00:00+00:00")


def test_fixture_capture_passes_validation():
    result = validate_capture(_capture())
    assert result["ok"], result["errors"]
    assert result["listings"] == 4
    assert result["variants"] == 6


def test_each_variant_becomes_its_own_candidate():
    result = _normalized()
    candidates = {(item["platform"], item["variant_id"]): item for item in result["candidates"]}

    base = candidates[("jd", "100001")]
    upgraded = candidates[("jd", "100001-32")]
    assert base["listing_id"] == upgraded["listing_id"] == "100001"
    assert base["price"] != upgraded["price"]
    assert base["attributes"]["ram_gb"] == 16
    assert upgraded["attributes"]["ram_gb"] == 32
    assert upgraded["attributes"]["storage_gb"] == 1000
    assert base["variant_label"] == "Ultra 5 / 16GB / 1TB"
    assert base["option_path"] == ["Ultra 5", "16GB", "1TB"]


def test_reviews_are_inherited_by_every_variant_of_a_listing():
    result = _normalized()
    for item in result["candidates"]:
        if item["listing_id"] != "100001":
            continue
        assert item["reviews"]["status"] == "ok"
        assert item["reviews"]["rating"] == 4.8
        assert item["reviews"]["sample_size"] == 20
        assert item["reviews"]["negative_rate"] == 0.2
        assert item["reviews"]["negative_themes"][0]["theme"] == "风扇噪音"


def test_hidden_price_stays_hidden_and_never_becomes_zero():
    result = _normalized()
    hidden = next(item for item in result["candidates"] if item["variant_id"] == "700002")
    assert hidden["price"] is None
    assert hidden["price_status"] == "hidden"
    assert hidden["needs_review"] is True


def test_capture_without_price_evidence_is_rejected():
    payload = _capture()
    payload["captures"][0]["variants"][0]["evidence"] = [
        {"field": "stock_status", "snippet": "现货", "source_url": "https://item.jd.com/100001.html"}
    ]
    result = validate_capture(payload)
    assert not result["ok"]
    assert any("snippet" in error for error in result["errors"])


def test_variant_without_option_path_is_rejected():
    payload = _capture()
    payload["captures"][0]["variants"][0]["option_path"] = []
    result = validate_capture(payload)
    assert not result["ok"]


def test_duplicate_variant_ids_are_rejected():
    payload = _capture()
    duplicate = json.loads(json.dumps(payload["captures"][0]["variants"][0]))
    payload["captures"][0]["variants"].append(duplicate)
    result = validate_capture(payload)
    assert not result["ok"]
    assert any("variant_id" in error for error in result["errors"])


def test_non_ok_page_state_is_rejected():
    payload = _capture()
    payload["captures"][1]["page_state"] = "captcha"
    result = validate_capture(payload)
    assert not result["ok"]
    assert any("page_state" in error for error in result["errors"])


def test_bare_list_is_accepted_for_compatibility():
    result = normalize_payload(_capture()["captures"], fetched_at="2026-10-29T00:00:00+00:00")
    assert result["summary"]["total"] == 6
    assert result["summary"]["listings"] == 4


def test_missing_reviews_stay_unknown():
    result = _normalized()
    no_reviews = next(item for item in result["candidates"] if item["listing_id"] == "100003")
    assert no_reviews["reviews"]["status"] == "missing"
    assert no_reviews["reviews"]["rating"] is None
    assert no_reviews["detail"]["warranty"] == UNKNOWN


def test_parameter_section_is_merged_and_after_sales_is_kept_verbatim():
    payload = _capture()
    capture = payload["captures"][0]
    for variant in capture["variants"]:
        variant["specs"].pop("保修", None)
    capture["sections"]["after_sales"] = "整机三年保修，上门服务"
    result = normalize_capture(payload, fetched_at="2026-10-29T00:00:00+00:00")
    item = next(c for c in result["candidates"] if c["variant_id"] == "100001")
    assert item["attributes"]["ports"] == "USB-C / HDMI"
    # After-sales prose is context, not a warranty commitment.
    assert item["detail"]["warranty"] == UNKNOWN
    assert "三年保修" in item["detail"]["after_sales"]


def test_brief_exclusions_are_applied_before_scoring():
    from score_candidates import score_candidates

    payload = _capture()
    payload["captures"][2]["title"] = "拆机 ThinkPad T14 20TB 企业级硬盘"
    normalized = normalize_capture(payload, fetched_at="2026-10-29T00:00:00+00:00")
    scorecard = {
        "name": "exclusion test",
        "category": "test",
        "version": "1",
        "hard_filters": [],
        "dimensions": [
            {"id": "price", "label": "价格", "weight": 1.0, "method": "minmax", "field": "price", "direction": "lower_better", "missing": "worst"}
        ],
    }
    brief = {"exclusions": ["拆机"]}
    scored = score_candidates(normalized["candidates"], scorecard, brief)
    excluded = next(c for c in scored["candidates"] if c["variant_id"] == "700002")
    assert excluded["eligible"] is False
    assert any("排除项" in reason for reason in excluded["elimination_reasons"])


def test_aborted_capture_is_kept_but_never_scored():
    from score_candidates import score_candidates

    payload = _capture()
    capture = payload["captures"][2]
    capture["aborted"] = True
    capture["abort_reason"] = "标题含二手，停止采集"
    normalized = normalize_capture(payload, fetched_at="2026-10-29T00:00:00+00:00")
    item = next(c for c in normalized["candidates"] if c["variant_id"] == "700002")
    assert item["excluded_early"] is True
    scorecard = {
        "name": "abort test",
        "category": "test",
        "version": "1",
        "hard_filters": [],
        "dimensions": [
            {"id": "price", "label": "价格", "weight": 1.0, "method": "minmax", "field": "price", "direction": "lower_better", "missing": "worst"}
        ],
    }
    scored = score_candidates(normalized["candidates"], scorecard, {})
    excluded = next(c for c in scored["candidates"] if c["variant_id"] == "700002")
    assert excluded["eligible"] is False
    assert any("采集阶段" in reason for reason in excluded["elimination_reasons"])
