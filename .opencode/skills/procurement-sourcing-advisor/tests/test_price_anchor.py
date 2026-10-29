from __future__ import annotations

from price_anchor import apply_price_anchors
from score_candidates import score_candidates


def _candidate(variant: str, shop: str, price: float, condition: str = "UNKNOWN") -> dict:
    return {
        "platform": "jd",
        "listing_id": "L1",
        "product_id": variant,
        "variant_id": variant,
        "variant_label": variant,
        "title": "希捷 ST20000NM002H 20TB 企业级硬盘",
        "model": "ST20000NM002H",
        "price": price,
        "price_status": "visible",
        "currency": "CNY",
        "shop_name": shop,
        "sales": "UNKNOWN",
        "url": f"https://item.jd.com/{variant}.html",
        "detail": {"specs": "UNKNOWN", "warranty": "5年", "invoice": "UNKNOWN", "stock_status": "UNKNOWN"},
        "attributes": {"condition": condition} if condition != "UNKNOWN" else {},
        "reviews": {"status": "missing", "sample_size": 0},
        "fetched_at": "2026-10-29T00:00:00+00:00",
        "confidence": 0.7,
    }


def test_official_price_becomes_the_anchor_and_cheap_outlier_is_flagged():
    cheap = _candidate("A", "硬盘商城20", 4379)
    official = _candidate("B", "希捷Seagate京东自营旗舰店", 8949.01)
    result = apply_price_anchors([cheap, official], min_ratio=0.7)

    assert official["price_anchor"]["value"] == 8949.01
    assert official["price_ratio"] == 1.0
    assert official["price_anomaly"] is False

    assert cheap["price_anchor"]["value"] == 8949.01
    assert cheap["price_ratio"] == 0.489
    assert cheap["price_anomaly"] is True
    assert result["anomalies"][0]["variant_id"] == "A"


def test_anchor_uses_median_of_multiple_official_listings():
    a = _candidate("A", "希捷Seagate京东自营旗舰店", 8000)
    b = _candidate("B", "希捷官方旗舰店", 9000)
    c = _candidate("C", "某第三方店", 4000)
    apply_price_anchors([a, b, c], min_ratio=0.7)
    assert c["price_anchor"]["value"] == 8500
    assert c["price_anomaly"] is True


def test_anomalous_price_cannot_earn_full_price_points():
    cheap = _candidate("A", "硬盘商城20", 4379)
    official = _candidate("B", "希捷Seagate京东自营旗舰店", 8949.01)
    apply_price_anchors([cheap, official], min_ratio=0.7)

    scorecard = {
        "name": "anchor cap",
        "category": "test",
        "version": "1",
        "hard_filters": [],
        "dimensions": [
            {
                "id": "price",
                "label": "价格",
                "weight": 1.0,
                "method": "minmax",
                "field": "price",
                "direction": "lower_better",
                "missing": "worst",
            }
        ],
    }
    scored = score_candidates([cheap, official], scorecard, {})
    by_id = {item["product_id"]: item for item in scored["candidates"]}

    assert by_id["A"]["price_anomaly"] is True
    assert by_id["A"]["score"] == 20.0  # capped at 1.0/5
    assert by_id["A"]["needs_review"] is True
    assert by_id["B"]["score"] == 20.0

    # Verified new + invoice lifts the cap.
    cheap["attributes"]["condition"] = "全新"
    cheap["detail"]["invoice"] = "可开增值税专用发票"
    scored = score_candidates([cheap, official], scorecard, {})
    by_id = {item["product_id"]: item for item in scored["candidates"]}
    assert by_id["A"]["score"] == 100.0
