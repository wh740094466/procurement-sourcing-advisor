"""Build per-listing variant matrices.

Buyers need to see "same link, different configurations, different prices" as a
table, not as a single number.  This module groups candidates by listing and
reports which attributes actually differ between their variants.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    from .common import UNKNOWN, normalize_text
except ImportError:  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import UNKNOWN, normalize_text  # type: ignore

# Attributes that are useful for telling configurations apart.  Order matters
# only for display.
VARIANT_ATTRIBUTES = (
    "condition",
    "warranty",
    "model",
    "screen_inch",
    "resolution",
    "panel",
    "ram_gb",
    "storage_gb",
    "cpu",
    "stand",
    "color",
    "capacity",
    "rpm",
    "cache",
    "size",
)


def _value(candidate: dict[str, Any], key: str) -> str:
    if key in {"warranty", "invoice", "stock_status"}:
        value = (candidate.get("detail") or {}).get(key)
    elif key == "model":
        value = candidate.get("model")
    else:
        value = (candidate.get("attributes") or {}).get(key)
    if value is None or normalize_text(value) == "":
        return UNKNOWN
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return normalize_text(value)


def build_variant_matrix(candidates: list[dict[str, Any]], *, min_variants: int = 2) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for candidate in candidates:
        listing_id = normalize_text(candidate.get("listing_id")) or normalize_text(candidate.get("product_id"))
        key = (normalize_text(candidate.get("platform")), listing_id)
        groups.setdefault(key, []).append(candidate)

    matrices: list[dict[str, Any]] = []
    for (platform, listing_id), items in groups.items():
        if len(items) < min_variants:
            continue
        differing = [
            key
            for key in VARIANT_ATTRIBUTES
            if len({_value(item, key) for item in items}) > 1
        ]
        rows = []
        for item in sorted(items, key=lambda entry: (entry.get("score") is None, -(entry.get("score") or 0))):
            rows.append(
                {
                    "variant_id": item.get("variant_id") or item.get("product_id"),
                    "variant_label": item.get("variant_label"),
                    "price": item.get("price"),
                    "price_status": item.get("price_status"),
                    "stock_status": (item.get("detail") or {}).get("stock_status", UNKNOWN),
                    "eligible": item.get("eligible"),
                    "fit_score": item.get("fit_score"),
                    "value_score": item.get("value_score"),
                    "status": (
                        "淘汰"
                        if not item.get("eligible")
                        else ("需求未满足" if item.get("fit_tier") == "below_fit" else "合格")
                    ),
                    "differences": {key: _value(item, key) for key in differing},
                }
            )
        matrices.append(
            {
                "platform": platform,
                "listing_id": listing_id,
                "title": items[0].get("listing_title") or items[0].get("title"),
                "url": items[0].get("url"),
                "variant_count": len(items),
                "differing_attributes": differing,
                "variants": rows,
            }
        )
    matrices.sort(
        key=lambda item: (
            -sum(1 for row in item["variants"] if row.get("eligible")),
            -item["variant_count"],
            item["platform"],
            item["listing_id"],
        )
    )
    return matrices
