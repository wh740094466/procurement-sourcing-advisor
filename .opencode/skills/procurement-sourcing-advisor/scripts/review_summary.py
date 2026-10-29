"""Deterministic review summarisation.

The model is allowed to read review text and group it into themes, but the
counts, rates and rankings are computed here so the same capture always yields
the same numbers.  "No review evidence" stays ``UNKNOWN``; it never becomes
"good reputation".
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    from .common import normalize_text
except ImportError:  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import normalize_text  # type: ignore


def summarize_reviews(reviews: Any) -> dict[str, Any]:
    if not isinstance(reviews, dict) or reviews.get("status") != "ok":
        return {
            "status": "missing",
            "rating": None,
            "positive_rate": None,
            "rating_count": None,
            "sample_size": 0,
            "negative_rate": None,
            "headline": "UNKNOWN（未采集到评价证据）",
            "top_negative": [],
        }

    negatives = [item for item in (reviews.get("negative_themes") or []) if isinstance(item, dict)]
    negatives = sorted(negatives, key=lambda item: (-int(item.get("count") or 0), normalize_text(item.get("theme"))))
    top_negative = negatives[:3]
    rating = reviews.get("rating")
    positive_rate = reviews.get("positive_rate")
    count = reviews.get("rating_count")
    sample = int(reviews.get("sample_size") or 0)
    rate = reviews.get("negative_rate")

    if rating is not None:
        metric = f"评分 {rating}"
    elif positive_rate is not None:
        metric = f"好评率 {positive_rate}%"
    else:
        metric = "评分 UNKNOWN"

    if top_negative:
        themes = "、".join(f"{item['theme']}×{int(item.get('count') or 1)}" for item in top_negative)
        headline = f"{metric}（{count if count is not None else 'UNKNOWN'} 条），样本 {sample} 条；主要负评：{themes}"
    else:
        headline = f"{metric}（{count if count is not None else 'UNKNOWN'} 条），样本 {sample} 条；样本中未出现集中负评"

    if rate is not None:
        headline += f"；负评率 {round(float(rate) * 100, 1)}%（样本口径）"

    return {
        "status": "ok",
        "rating": rating,
        "positive_rate": positive_rate,
        "rating_count": count,
        "sample_size": sample,
        "negative_rate": rate,
        "headline": headline,
        "top_negative": top_negative,
    }
