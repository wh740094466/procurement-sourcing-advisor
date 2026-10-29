"""Assign ranks lexicographically: fit first, then value, then price.

Requirement evaluation happens earlier (``requirement_fit.evaluate_all``) and
value computation happens in ``value_analysis.analyze_value``; this module only
orders candidates that already passed both gates.
"""

from __future__ import annotations

from typing import Any


def assign_ranks(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    for item in candidates:
        item["rank"] = None

    pool = [item for item in candidates if item.get("fit_tier") == "pass"]
    pool.sort(
        key=lambda item: (
            -float(item.get("fit_score") or 0),
            -float(item.get("value_score") or 0),
            float(item.get("price") or float("inf")),
        )
    )
    for index, item in enumerate(pool, start=1):
        item["rank"] = index

    return {
        "ranked": len(pool),
        "below_fit": [
            item.get("variant_id") or item.get("product_id")
            for item in candidates
            if item.get("fit_tier") == "below_fit"
        ],
        "must_failed": [
            item.get("variant_id") or item.get("product_id")
            for item in candidates
            if item.get("fit_tier") == "must_fail"
        ],
    }
