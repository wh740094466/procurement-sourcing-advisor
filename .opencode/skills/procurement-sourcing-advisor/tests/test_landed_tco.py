from __future__ import annotations

from landed_price import compute_landed_price
from tco_calc import compute_tco


def _candidate(price=1000.0):
    return {
        "platform": "jd",
        "product_id": "1",
        "price": price,
        "price_status": "visible" if price else "hidden",
        "landed_price": {},
    }


def test_landed_price_does_not_invent_shipping_tax_or_coupon():
    result = compute_landed_price(_candidate(), {})
    assert result["landed_price"] is None
    assert result["floor"] == 1000.0
    assert result["status"] == "partial"
    assert "shipping" in result["missing_components"]
    assert "tax" in result["missing_components"]
    assert "coupon" in result["missing_components"]


def test_landed_price_exact_when_all_components_supplied():
    result = compute_landed_price(
        _candidate(),
        {
            "price_includes_tax": True,
            "shipping_cny": 20,
            "coupon_cny": 50,
            "other_fees_cny": 0,
        },
    )
    assert result["landed_price"] == 970.0
    assert result["status"] == "exact"
    assert result["missing_components"] == []


def test_tax_rate_without_inclusion_flag_is_ambiguous():
    result = compute_landed_price(
        _candidate(),
        {
            "tax_rate": 0.13,
            "shipping_cny": 0,
            "coupon_cny": 0,
            "other_fees_cny": 0,
        },
    )
    assert result["landed_price"] is None
    assert "tax" in result["missing_components"]


def test_tco_exact_and_partial():
    candidate = _candidate()
    candidate["landed_price"] = compute_landed_price(
        candidate,
        {
            "price_includes_tax": True,
            "shipping_cny": 0,
            "coupon_cny": 0,
            "other_fees_cny": 0,
        },
    )
    exact = compute_tco(
        candidate,
        {
            "years": 3,
            "defaults": {
                "consumables_per_year_cny": 100,
                "energy_per_year_cny": 50,
                "maintenance_per_year_cny": 20,
                "accessories_cny": 10,
                "resale_value_cny": 200,
            },
        },
    )
    assert exact["status"] == "exact"
    assert exact["exact_total"] == 1000 + 300 + 150 + 60 + 10 - 200
    assert exact["missing_components"] == []

    partial = compute_tco(candidate, {"years": 3})
    assert partial["status"] == "partial"
    assert partial["exact_total"] is None
    assert partial["floor"] == 1000.0
