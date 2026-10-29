"""Compute a *guarded* landed price.

The captured page price is a single configuration's sticker price.  It does not
include account coupons, shipping, tax, or checkout fees.  Therefore this module
refuses to call a page price a "final landed price" unless every component is
supplied explicitly.  When data is incomplete it emits a floor and, when safe, a
ceiling, plus the exact list of missing components.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

try:
    from .common import get_path, load_json, normalize_text, round_money, save_json
except ImportError:  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import get_path, load_json, normalize_text, round_money, save_json  # type: ignore


COST_KEYS = (
    "shipping_cny",
    "tax_rate",
    "tax_included",
    "price_includes_tax",
    "price_includes_shipping",
    "coupon_cny",
    "other_fees_cny",
)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _product_key(candidate: dict[str, Any]) -> str:
    return f"{candidate.get('platform', '')}:{candidate.get('product_id', '')}"


def _costs_for(candidate: dict[str, Any], costs_doc: dict[str, Any] | None) -> dict[str, Any]:
    costs_doc = costs_doc or {}
    defaults = costs_doc.get("defaults", {}) if isinstance(costs_doc.get("defaults"), dict) else {}
    by_product = costs_doc.get("by_product", {}) if isinstance(costs_doc.get("by_product"), dict) else {}
    key = _product_key(candidate)
    product_costs = by_product.get(key) or by_product.get(str(candidate.get("product_id"))) or {}
    inline = candidate.get("costs") if isinstance(candidate.get("costs"), dict) else {}
    merged = {}
    for source in (defaults, product_costs, inline):
        if isinstance(source, dict):
            merged.update({k: v for k, v in source.items() if k in COST_KEYS})
    return merged


def compute_landed_price(candidate: dict[str, Any], costs: dict[str, Any] | None = None) -> dict[str, Any]:
    costs = costs or {}
    base = _number(candidate.get("price"))
    base_known = base is not None and base > 0

    shipping: float | None
    if costs.get("price_includes_shipping") is True:
        shipping, shipping_known = 0.0, True
    elif costs.get("shipping_cny") is not None:
        shipping, shipping_known = max(0.0, _number(costs.get("shipping_cny")) or 0.0), True
    else:
        shipping, shipping_known = None, False

    tax: float | None
    tax_known = False
    if costs.get("price_includes_tax") is True or costs.get("tax_included") is True:
        tax, tax_known = 0.0, True
    elif costs.get("price_includes_tax") is False or costs.get("tax_included") is False:
        rate = _number(costs.get("tax_rate"))
        if rate is not None and base_known:
            tax, tax_known = max(0.0, base * rate), True
        else:
            tax = None
    else:
        # A tax rate without an explicit tax-inclusion flag is ambiguous: the
        # listing may already include VAT.  Do not add it silently.
        tax = None

    coupon = _number(costs.get("coupon_cny"))
    coupon_known = coupon is not None
    coupon_value = max(0.0, coupon or 0.0)

    other = _number(costs.get("other_fees_cny"))
    other_known = other is not None
    other_value = max(0.0, other or 0.0)

    missing: list[str] = []
    if not base_known:
        missing.append("base_price")
    if not shipping_known:
        missing.append("shipping")
    if not tax_known:
        missing.append("tax")
    if not coupon_known:
        missing.append("coupon")
    if not other_known:
        missing.append("other_fees")

    known_adders = (shipping or 0.0) + (tax or 0.0) + (other if other_known else 0.0)
    floor = round_money(base + known_adders - (coupon_value if coupon_known else 0.0)) if base_known else None

    # A ceiling is only meaningful when every non-coupon component is known.
    # An unknown coupon can be treated as zero for the ceiling, but an unknown
    # shipping/tax/fee cannot be assumed away.
    if base_known and shipping_known and tax_known and other_known:
        ceiling = round_money(base + known_adders - (coupon_value if coupon_known else 0.0))
    else:
        ceiling = None

    exact = None
    if not missing:
        exact = max(0.0, round_money(base + known_adders - coupon_value) or 0.0)

    if exact is not None:
        status = "exact"
        note = "所有费用项均已显式提供；仍建议以结算页复核"
    elif base_known:
        status = "partial"
        note = "仅有检索价/详情价；未含或未确认：" + "、".join(missing)
    else:
        status = "unknown"
        note = "价格缺失或隐藏，无法计算到手价；不得按 0 处理"

    components = {
        "base_price": round_money(base),
        "shipping": round_money(shipping),
        "tax": round_money(tax),
        "coupon": round_money(coupon_value) if coupon_known else None,
        "other_fees": round_money(other_value) if other_known else None,
    }
    completeness = 1.0 - (len(missing) / 5.0)

    return {
        "observable_price": round_money(base),
        "landed_price": exact,
        "floor": floor,
        "ceiling": ceiling,
        "range": {"min": floor, "max": ceiling},
        "status": status,
        "known_components": components,
        "missing_components": missing,
        "completeness": round(max(0.0, completeness), 2),
        "note": note,
    }


def apply_landed_prices(candidates: list[dict[str, Any]], costs_doc: dict[str, Any] | None = None) -> dict[str, Any]:
    warnings: list[str] = []
    output: list[dict[str, Any]] = []
    for candidate in candidates:
        enriched = dict(candidate)
        costs = _costs_for(candidate, costs_doc)
        enriched["landed_price"] = compute_landed_price(candidate, costs)
        lp = enriched["landed_price"]
        if lp["status"] == "partial":
            warnings.append(
                f"{candidate.get('platform')}:{candidate.get('product_id')} 到手价为下限/区间，缺少：{', '.join(lp['missing_components'])}"
            )
        elif lp["status"] == "unknown":
            warnings.append(
                f"{candidate.get('platform')}:{candidate.get('product_id')} 价格不可用，未计算到手价"
            )
        output.append(enriched)
    return {"candidates": output, "warnings": warnings, "errors": []}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute guarded landed prices")
    parser.add_argument("--input", required=True, help="normalized candidates JSON")
    parser.add_argument("--costs", default=None, help="cost model JSON")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    payload = load_json(args.input)
    candidates = payload.get("candidates", payload) if isinstance(payload, dict) else payload
    costs = load_json(args.costs) if args.costs else {}
    result = apply_landed_prices(candidates, costs)
    save_json(args.output, result)
    print(f"priced {len(result['candidates'])} candidates, warnings={len(result['warnings'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
