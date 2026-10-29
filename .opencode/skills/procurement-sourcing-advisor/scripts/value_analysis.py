"""Compute performance, price index, value score and the Pareto frontier.

Requirement fit decides *whether* a candidate may compete.  Value decides the
order among candidates that already meet the needs: performance per unit of
price, with price expressed against the official anchor (so scores do not drift
when the candidate pool changes).

Pareto rule: A dominates B when A is no more expensive and no worse on
performance, and strictly better on at least one of them.
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path
from typing import Any

try:
    from .common import normalize_text
    from .score_candidates import _condition_verified
except ImportError:  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import normalize_text  # type: ignore
    from score_candidates import _condition_verified  # type: ignore

ANOMALY_VALUE_PENALTY = 0.15


def _performance(candidate: dict[str, Any], value_dims: dict[str, float]) -> float | None:
    breakdown = {
        item.get("dimension_id"): item
        for item in (candidate.get("score_breakdown") or [])
        if isinstance(item, dict)
    }
    total_weight = 0.0
    earned = 0.0
    for dim_id, weight in value_dims.items():
        item = breakdown.get(dim_id)
        if not item or not item.get("known"):
            continue
        total_weight += weight
        earned += weight * float(item.get("normalized_score") or 0)
    if total_weight <= 0:
        return None
    return round(earned / total_weight, 4)


def analyze_value(
    candidates: list[dict[str, Any]],
    dimensions: list[dict[str, Any]],
    *,
    fit_threshold: float = 0.8,
) -> dict[str, Any]:
    value_dims = {
        dim.get("id"): float(dim.get("weight") or 0)
        for dim in dimensions
        if isinstance(dim, dict) and dim.get("group") == "value"
    }

    pool = [
        item
        for item in candidates
        if item.get("eligible")
        and item.get("fit_tier") == "pass"
        and item.get("price_status") == "visible"
        and item.get("price")
    ]

    fallback_anchor = None
    if pool:
        fallback_anchor = round(statistics.median(float(item["price"]) for item in pool), 2)

    for item in candidates:
        performance = _performance(item, value_dims)
        item["performance"] = performance
        anchor = (item.get("price_anchor") or {}).get("value") or fallback_anchor
        if performance is not None and item.get("price") and anchor:
            price_index = float(item["price"]) / float(anchor)
            item["price_index"] = round(price_index, 4)
            effective_index = price_index
            if item.get("price_anomaly") and not _condition_verified(item):
                # An unverified sub-anchor price cannot buy extra value: neutralise
                # the price advantage first, then apply the risk discount.
                effective_index = max(price_index, 1.0)
                item["value_note"] = (
                    f"价格低于官方/自营锚点且成色/发票未证实，价格指数按 {effective_index:.2f} 计，"
                    f"并扣减 {ANOMALY_VALUE_PENALTY:.0%}"
                )
            value_score = performance / effective_index if effective_index > 0 else None
            if value_score is not None and item.get("price_anomaly") and not _condition_verified(item):
                value_score *= 1.0 - ANOMALY_VALUE_PENALTY
            item["value_score"] = round(value_score, 4) if value_score is not None else None
        else:
            item["price_index"] = None
            item["value_score"] = None
        item["dominated_by"] = []
        item["on_frontier"] = False

    frontier_pool = [
        item
        for item in pool
        if not (item.get("price_anomaly") and not _condition_verified(item))
    ]

    frontier: list[dict[str, Any]] = []
    for item in frontier_pool:
        if item.get("performance") is None or item.get("value_score") is None:
            continue
        dominators = []
        for other in frontier_pool:
            if other is item or other.get("performance") is None:
                continue
            cheaper_or_equal = float(other["price"]) <= float(item["price"]) + 1e-9
            better_or_equal = float(other["performance"]) >= float(item["performance"]) - 1e-9
            strictly_better = (
                float(other["price"]) < float(item["price"]) - 1e-9
                or float(other["performance"]) > float(item["performance"]) + 1e-9
            )
            if cheaper_or_equal and better_or_equal and strictly_better:
                dominators.append(other.get("variant_id") or other.get("product_id"))
        item["dominated_by"] = dominators
        if not dominators:
            item["on_frontier"] = True
            frontier.append(item)

    frontier.sort(key=lambda entry: (-float(entry.get("fit_score") or 0), float(entry["price"])))
    return {
        "value_dimensions": sorted(value_dims.keys()),
        "pool_size": len(pool),
        "fallback_anchor": fallback_anchor,
        "frontier": [
            {
                "variant_id": item.get("variant_id") or item.get("product_id"),
                "platform": item.get("platform"),
                "price": item.get("price"),
                "performance": item.get("performance"),
                "value_score": item.get("value_score"),
                "fit_score": item.get("fit_score"),
            }
            for item in frontier
        ],
        "dominated": [
            {
                "variant_id": item.get("variant_id") or item.get("product_id"),
                "price": item.get("price"),
                "performance": item.get("performance"),
                "dominated_by": item.get("dominated_by"),
            }
            for item in frontier_pool
            if item.get("dominated_by")
        ],
        "excluded_from_frontier": [
            {
                "variant_id": item.get("variant_id") or item.get("product_id"),
                "price": item.get("price"),
                "reason": item.get("value_note") or "价格异常且成色/发票未证实",
            }
            for item in pool
            if item.get("price_anomaly") and not _condition_verified(item)
        ],
    }
