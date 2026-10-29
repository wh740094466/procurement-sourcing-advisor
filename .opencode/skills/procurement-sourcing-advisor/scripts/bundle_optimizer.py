"""Evaluate single-order versus split-order options without inventing fees.

A split recommendation is only actionable when quantities, unit prices, and
shipping/coupon rules are available.  Otherwise the result is explicitly a
"subtotal only" or "floor" comparison and must not be presented as a guaranteed
saving.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

try:
    from .common import load_json, normalize_text, round_money, save_json
except ImportError:  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import load_json, normalize_text, round_money, save_json  # type: ignore


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _unit_price(candidate: dict[str, Any]) -> tuple[float | None, str]:
    landed = candidate.get("landed_price") or {}
    exact = _number(landed.get("landed_price"))
    if exact is not None:
        return exact, "exact"
    floor = _number(landed.get("floor"))
    if floor is not None:
        return floor, "floor"
    price = _number(candidate.get("price"))
    if price is not None:
        return price, "listing"
    return None, "unknown"


def _shipping(subtotal: float, options: dict[str, Any]) -> tuple[float | None, str]:
    thresholds = options.get("shipping_thresholds")
    if isinstance(thresholds, list):
        applicable = [
            item
            for item in thresholds
            if isinstance(item, dict)
            and _number(item.get("min_subtotal_cny")) is not None
            and subtotal >= float(item["min_subtotal_cny"])
        ]
        if applicable:
            chosen = max(applicable, key=lambda item: float(item["min_subtotal_cny"]))
            return max(0.0, float(chosen.get("shipping_cny", 0))), "exact"
        if options.get("default_shipping_cny") is not None:
            return max(0.0, float(options["default_shipping_cny"])), "exact"
        return None, "unknown"
    if options.get("default_shipping_cny") is not None:
        return max(0.0, float(options["default_shipping_cny"])), "exact"
    return None, "unknown"


def _coupon(subtotal: float, options: dict[str, Any]) -> tuple[float | None, str]:
    coupons = options.get("coupons")
    if not isinstance(coupons, list):
        return None, "unknown"
    applicable = [
        item
        for item in coupons
        if isinstance(item, dict)
        and _number(item.get("min_subtotal_cny")) is not None
        and subtotal >= float(item["min_subtotal_cny"])
    ]
    if not applicable:
        return 0.0, "exact"
    chosen = max(applicable, key=lambda item: float(item["min_subtotal_cny"]))
    return max(0.0, float(chosen.get("discount_cny", 0))), "exact"


def _order(
    candidate: dict[str, Any],
    quantity: int,
    options: dict[str, Any],
    *,
    stock_assumed: bool,
) -> dict[str, Any]:
    unit, unit_status = _unit_price(candidate)
    subtotal = round_money(unit * quantity) if unit is not None else None
    shipping = coupon = None
    shipping_status = coupon_status = "unknown"
    if subtotal is not None:
        shipping, shipping_status = _shipping(subtotal, options)
        coupon, coupon_status = _coupon(subtotal, options)

    total = None
    if subtotal is not None and shipping is not None and coupon is not None:
        total = max(0.0, round_money(subtotal + shipping - coupon) or 0.0)
    status = "exact" if total is not None else ("partial" if subtotal is not None else "unknown")
    missing = []
    if unit_status != "exact":
        missing.append("unit_price_exact")
    if shipping is None:
        missing.append("shipping")
    if coupon is None:
        missing.append("coupon")

    return {
        "platform": candidate.get("platform"),
        "product_id": candidate.get("product_id"),
        "title": candidate.get("title"),
        "rank": candidate.get("rank"),
        "score": candidate.get("score"),
        "quantity": quantity,
        "unit_price": round_money(unit),
        "unit_price_status": unit_status,
        "subtotal": subtotal,
        "shipping": round_money(shipping),
        "coupon": round_money(coupon),
        "total": total,
        "status": status,
        "missing_components": missing,
        "stock_assumed": stock_assumed,
        "url": candidate.get("url"),
    }


def _sum_orders(orders: list[dict[str, Any]]) -> dict[str, Any]:
    known_totals = [order["total"] for order in orders if order["total"] is not None]
    subtotals = [order["subtotal"] for order in orders if order["subtotal"] is not None]
    if orders and len(known_totals) == len(orders):
        total = round_money(sum(known_totals))
        status = "exact"
    elif subtotals:
        total = round_money(sum(subtotals))
        status = "partial"
    else:
        total = None
        status = "unknown"
    return {
        "orders": orders,
        "order_count": len(orders),
        "total": total,
        "status": status,
        "unit_count": sum(order["quantity"] for order in orders),
    }


def optimize_bundle(
    scored_candidates: list[dict[str, Any]],
    brief: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    brief = brief or {}
    options = options or {}
    quantity = max(1, int(brief.get("quantity") or options.get("quantity") or 1))
    allow_split = bool(brief.get("allow_split", options.get("allow_split", False)))
    max_orders = max(1, int(options.get("max_orders", 2)))
    eligible = [item for item in scored_candidates if item.get("eligible") and item.get("rank") is not None]
    eligible.sort(key=lambda item: int(item.get("rank") or 10**9))

    if not eligible:
        return {
            "quantity": quantity,
            "single_order": None,
            "split_order": None,
            "recommended_option": None,
            "summary": {"message": "没有通过硬过滤的候选，无法生成采购组合"},
            "warnings": ["没有通过硬过滤的候选"],
        }

    warnings: list[str] = []
    top = eligible[0]
    top_score = float(top.get("score") or 0)
    min_score_ratio = float(options.get("min_score_ratio", 0.9))
    split_pool = [
        item
        for item in eligible
        if float(item.get("score") or 0) >= top_score * min_score_ratio
    ]
    stock_assumed = all(_number(item.get("available_quantity")) is None for item in eligible)
    single = _sum_orders([_order(top, quantity, options, stock_assumed=stock_assumed)])
    single["strategy"] = "top_ranked_single_supplier"

    split = None
    if allow_split and quantity > 1 and len(eligible) > 1 and len(split_pool) > 1:
        max_qty_default = max(1, int(options.get("max_quantity_per_candidate") or quantity))
        remaining = quantity
        orders: list[dict[str, Any]] = []
        for candidate in sorted(
            split_pool,
            key=lambda item: (
                _unit_price(item)[0] if _unit_price(item)[0] is not None else float("inf"),
                int(item.get("rank") or 10**9),
            ),
        ):
            if remaining <= 0 or len(orders) >= max_orders:
                break
            available = _number(candidate.get("available_quantity"))
            capacity = max(1, int(available)) if available is not None else max_qty_default
            take = min(remaining, capacity)
            if take <= 0:
                continue
            orders.append(_order(candidate, take, options, stock_assumed=available is None))
            remaining -= take
        if remaining > 0:
            warnings.append(f"拆单方案仍缺 {remaining} 件，候选的可采购数量未知或不足")
        if orders:
            split = _sum_orders(orders)
            split["strategy"] = "cost_minimizing_split"
            split["remaining_quantity"] = remaining

    def rank_status(status: str) -> int:
        return {"exact": 0, "partial": 1, "unknown": 2}.get(status, 3)

    recommended = "single"
    savings = None
    savings_status = "unknown"
    if split and split.get("total") is not None and single.get("total") is not None:
        if rank_status(split["status"]) <= rank_status(single["status"]):
            savings = round_money(single["total"] - split["total"])
            savings_status = "exact" if split["status"] == single["status"] == "exact" else "floor_comparison"
            if savings is not None and savings > 0 and split["status"] == "exact":
                recommended = "split"
            elif savings is not None and savings > 0:
                # A lower floor is not proof of a lower final cost.
                recommended = "single"
                warnings.append("拆单仅按价格下限比较更低；运费/券/库存未完全确认，不能证明最终更省")
            else:
                recommended = "single"
    elif split:
        warnings.append("拆单方案缺少完整价格/费用，无法与单渠道方案做确定比较")

    if allow_split and quantity == 1:
        warnings.append("数量为 1，不存在拆单收益")
    if not allow_split:
        warnings.append("采购简报未允许拆单，未生成拆单方案")
    if allow_split and quantity > 1 and len(split_pool) < 2:
        warnings.append(
            f"没有足够多的高分候选可用于拆单（要求评分不低于最高分的 {min_score_ratio:.0%}），未生成拆单方案"
        )
    if stock_assumed:
        warnings.append("候选未提供库存/可采购数量，组合方案按可满足需求假设，需下单前确认")

    recommended_order = single if recommended == "single" else split
    return {
        "quantity": quantity,
        "allow_split": allow_split,
        "single_order": single,
        "split_order": split,
        "recommended_option": recommended,
        "recommended_order": recommended_order,
        "savings": savings,
        "savings_status": savings_status,
        "warnings": warnings,
        "assumptions": [
            "商品价格来自检索/详情/浏览器补证，未含账号券、运费、税费时以 landed_price 状态为准",
            "库存与可采购数量未由 MCP 保证" if stock_assumed else "部分候选提供了可采购数量",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate single vs split procurement orders")
    parser.add_argument("--candidates", required=True, help="scored candidates JSON")
    parser.add_argument("--brief", default=None)
    parser.add_argument("--options", default=None)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    payload = load_json(args.candidates)
    candidates = payload.get("candidates", payload) if isinstance(payload, dict) else payload
    brief = load_json(args.brief) if args.brief else {}
    options = load_json(args.options) if args.options else {}
    result = optimize_bundle(candidates, brief, options)
    save_json(args.output, result)
    print(f"bundle recommendation={result.get('recommended_option')}, warnings={len(result.get('warnings', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
