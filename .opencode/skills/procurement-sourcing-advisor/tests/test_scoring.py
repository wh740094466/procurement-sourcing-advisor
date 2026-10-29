from __future__ import annotations

import json

import pytest

from conftest import FIXTURES
from landed_price import apply_landed_prices
from normalize_candidates import normalize_capture
from rank_candidates import assign_ranks
from requirement_fit import evaluate_all
from score_candidates import score_candidates, validate_scorecard
from tco_calc import apply_tco
from value_analysis import analyze_value


def _scored():
    capture = json.loads((FIXTURES / "raw_capture.json").read_text(encoding="utf-8"))
    brief = json.loads((FIXTURES / "brief.json").read_text(encoding="utf-8"))
    scorecard = json.loads((FIXTURES / "scorecard.json").read_text(encoding="utf-8"))
    costs = json.loads((FIXTURES / "costs.json").read_text(encoding="utf-8"))
    tco_model = json.loads((FIXTURES / "tco_model.json").read_text(encoding="utf-8"))
    normalized = normalize_capture(capture, fetched_at="2026-10-29T00:00:00+00:00")
    priced = apply_landed_prices(normalized["candidates"], costs)
    with_tco = apply_tco(priced["candidates"], tco_model)
    scored = score_candidates(with_tco["candidates"], scorecard, brief, assign_rank=False)
    evaluate_all(scored["candidates"], brief.get("requirements") or [], fit_threshold=0.8)
    analyze_value(scored["candidates"], scorecard.get("dimensions") or [], fit_threshold=0.8)
    assign_ranks(scored["candidates"])
    return scored


def test_fixture_ranking_and_hard_filters():
    result = _scored()
    by_id = {item["product_id"]: item for item in result["candidates"]}
    top = sorted(
        [item for item in result["candidates"] if item["rank"] is not None],
        key=lambda item: item["rank"],
    )[0]
    assert top["product_id"] == "100001"
    assert by_id["100003"]["eligible"] is False
    assert any("广告" in reason for reason in by_id["100003"]["elimination_reasons"])
    assert by_id["700002"]["eligible"] is False
    assert any("二手" in reason for reason in by_id["700002"]["elimination_reasons"])
    assert by_id["700003"]["price"] == 5599.0
    assert all(0 <= item["score"] <= 100 for item in result["candidates"])


def test_expensive_variant_of_same_listing_is_filtered_separately():
    result = _scored()
    by_id = {item["product_id"]: item for item in result["candidates"]}
    assert by_id["100001"]["eligible"] is True
    # Two configurations of the same listing can both qualify.
    assert by_id["100001-24"]["eligible"] is True
    assert by_id["100001-32"]["eligible"] is False
    assert any("实际值 6299" in reason for reason in by_id["100001-32"]["elimination_reasons"])


def test_reviews_dimension_scores_rating_and_penalty():
    scorecard = {
        "name": "review dimension",
        "category": "test",
        "version": "1",
        "hard_filters": [],
        "dimensions": [
            {
                "id": "reviews",
                "label": "评价",
                "weight": 1.0,
                "method": "reviews",
                "field": "reviews.rating",
                "negative_rate_penalty": 2.0,
                "missing": "worst",
            }
        ],
    }
    candidate = {
        "platform": "jd",
        "listing_id": "1",
        "product_id": "1",
        "variant_id": "1",
        "variant_label": "base",
        "title": "x",
        "model": "UNKNOWN",
        "price": 100,
        "price_status": "visible",
        "currency": "CNY",
        "shop_name": "UNKNOWN",
        "sales": "UNKNOWN",
        "url": "",
        "detail": {"specs": "UNKNOWN", "warranty": "UNKNOWN", "invoice": "UNKNOWN", "stock_status": "UNKNOWN"},
        "reviews": {
            "status": "ok",
            "rating": 4.8,
            "rating_count": 100,
            "sample_size": 10,
            "negative_rate": 0.3,
            "negative_themes": [],
        },
        "fetched_at": "2026-10-29T00:00:00+00:00",
        "confidence": 0.5,
    }
    result = score_candidates([candidate], scorecard, {})
    # rating 4.8 -> 5 points, minus 2 * 0.3 = 0.6 penalty -> 4.4/5 = 88
    assert result["candidates"][0]["score"] == pytest.approx(88.0, abs=0.01)

    candidate["reviews"]["status"] = "missing"
    result = score_candidates([candidate], scorecard, {})
    assert result["candidates"][0]["score"] == 0
    assert "reviews" in result["candidates"][0]["missing_dimensions"]


def test_invalid_weight_sum_is_rejected():
    scorecard = json.loads((FIXTURES / "scorecard.json").read_text(encoding="utf-8"))
    scorecard["dimensions"][0]["weight"] = 0.5
    errors, _ = validate_scorecard(scorecard)
    assert any("sum to 1.0" in error for error in errors)
    with pytest.raises(ValueError):
        score_candidates([], scorecard, {})


def test_manual_score_requires_evidence():
    scorecard = {
        "name": "manual test",
        "category": "test",
        "version": "1",
        "hard_filters": [],
        "dimensions": [
            {
                "id": "fit",
                "label": "Fit",
                "weight": 1.0,
                "method": "manual",
                "missing": "worst",
                "evidence_required": True,
            }
        ],
    }
    candidate = {
        "platform": "jd",
        "product_id": "1",
        "title": "x",
        "model": "UNKNOWN",
        "price": 100,
        "price_status": "visible",
        "currency": "CNY",
        "shop_name": "UNKNOWN",
        "sales": "UNKNOWN",
        "url": "",
        "detail": {"specs": "UNKNOWN", "warranty": "UNKNOWN", "invoice": "UNKNOWN", "stock_status": "UNKNOWN"},
        "fetched_at": "2026-10-29T00:00:00+00:00",
        "confidence": 0.5,
        "manual_scores": {"fit": {"score": 5, "evidence": ""}},
    }
    result = score_candidates([candidate], scorecard, {})
    assert result["candidates"][0]["score"] == 0
    assert "fit" in result["candidates"][0]["missing_dimensions"]

    candidate["manual_scores"]["fit"]["evidence"] = "页面写明人体工学腰托"
    result = score_candidates([candidate], scorecard, {})
    assert result["candidates"][0]["score"] == 100


def test_missing_hard_filter_review_does_not_eliminate():
    scorecard = {
        "name": "review test",
        "category": "test",
        "version": "1",
        "hard_filters": [
            {"id": "budget", "field": "landed_price.floor", "op": "lte", "value": 1000, "missing": "review"}
        ],
        "dimensions": [
            {"id": "price", "label": "Price", "weight": 1.0, "method": "thresholds", "field": "price", "thresholds": [{"max": 1000, "score": 5}], "missing": "worst"}
        ],
    }
    candidate = {
        "platform": "jd",
        "product_id": "1",
        "title": "x",
        "model": "UNKNOWN",
        "price": 100,
        "price_status": "visible",
        "currency": "CNY",
        "shop_name": "UNKNOWN",
        "sales": "UNKNOWN",
        "url": "",
        "detail": {"specs": "UNKNOWN", "warranty": "UNKNOWN", "invoice": "UNKNOWN", "stock_status": "UNKNOWN"},
        "fetched_at": "2026-10-29T00:00:00+00:00",
        "confidence": 0.5,
    }
    result = score_candidates([candidate], scorecard, {})
    assert result["candidates"][0]["eligible"] is True
    assert result["candidates"][0]["needs_review"] is True
