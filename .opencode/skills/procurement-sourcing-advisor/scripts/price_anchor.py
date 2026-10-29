"""Compute an official/self-operated price anchor per model.

Marketplace prices are not a single transparent number: the same model is sold
as official retail, third-party new, seller-refurbished ("官换"), and used.
An unusually cheap listing must therefore be compared against an official
anchor before it can win on price.

The anchor is deliberately conservative:

* only 自营 / 官方旗舰店 / 官方直营 listings are used
* the median of their prices is the anchor (one promo outlier cannot move it)
* a candidate below ``min_ratio`` of the anchor is flagged, not deleted
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path
from typing import Any

try:
    from .common import UNKNOWN, normalize_text
except ImportError:  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import UNKNOWN, normalize_text  # type: ignore

OFFICIAL_MARKERS = ("自营", "官方旗舰", "官方直营")
DEFAULT_MIN_RATIO = 0.7


def is_official(candidate: dict[str, Any]) -> bool:
    shop = normalize_text(candidate.get("shop_name"))
    return any(marker in shop for marker in OFFICIAL_MARKERS)


def _group_key(candidate: dict[str, Any]) -> str:
    model = normalize_text(candidate.get("model"))
    if model and model != UNKNOWN:
        return f"model:{model.lower()}"
    return f"listing:{candidate.get('platform')}:{candidate.get('listing_id')}"


def apply_price_anchors(
    candidates: list[dict[str, Any]],
    *,
    min_ratio: float = DEFAULT_MIN_RATIO,
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        groups.setdefault(_group_key(candidate), []).append(candidate)

    anchors: list[dict[str, Any]] = []
    for key, items in groups.items():
        officials = [
            item
            for item in items
            if is_official(item) and item.get("price_status") == "visible" and item.get("price")
        ]
        if not officials:
            for item in items:
                item["price_anchor"] = None
                item["price_ratio"] = None
                item["price_anomaly"] = False
            continue

        prices = [float(item["price"]) for item in officials]
        anchor = round(statistics.median(prices), 2)
        # Name the shop that actually sits at the anchor when possible.
        anchor_item = min(officials, key=lambda item: abs(float(item["price"]) - anchor))
        has_comparison = any(not is_official(item) for item in items)
        if has_comparison:
            anchors.append(
                {
                    "group": key,
                    "anchor_price": anchor,
                    "sample_size": len(officials),
                    "source_shop": anchor_item.get("shop_name"),
                    "source_url": anchor_item.get("url"),
                    "models": sorted({normalize_text(item.get("model")) for item in items}),
                }
            )

        for item in items:
            item["price_anchor"] = {
                "value": anchor,
                "source": "official_median",
                "shop": anchor_item.get("shop_name"),
                "url": anchor_item.get("url"),
                "sample_size": len(officials),
            }
            if item.get("price_status") == "visible" and item.get("price"):
                ratio = float(item["price"]) / anchor if anchor else None
                item["price_ratio"] = round(ratio, 3) if ratio is not None else None
                item["price_anomaly"] = bool(ratio is not None and ratio < min_ratio)
            else:
                item["price_ratio"] = None
                item["price_anomaly"] = False

    return {
        "min_ratio": min_ratio,
        "anchors": anchors,
        "anomalies": [
            {
                "platform": item.get("platform"),
                "variant_id": item.get("variant_id") or item.get("product_id"),
                "model": item.get("model"),
                "price": item.get("price"),
                "anchor": (item.get("price_anchor") or {}).get("value"),
                "ratio": item.get("price_ratio"),
                "shop": item.get("shop_name"),
            }
            for item in candidates
            if item.get("price_anomaly")
        ],
    }
