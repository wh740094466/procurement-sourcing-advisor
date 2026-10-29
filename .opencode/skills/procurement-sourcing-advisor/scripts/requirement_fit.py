"""Evaluate user requirements against candidates.

Two layers:

* ``must``  — a gate.  A failed must removes the candidate from ranking.
* ``should`` — the fit score.  Usage needs live here, with explicit weights.

Unknown values are not failures.  They receive partial credit (default 0.5) and
are listed separately so the report can ask for confirmation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    from .common import UNKNOWN, get_path, is_missing, normalize_text
    from .score_candidates import _compare
except ImportError:  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import UNKNOWN, get_path, is_missing, normalize_text  # type: ignore
    from score_candidates import _compare  # type: ignore


def _evaluate_one(candidate: dict[str, Any], requirement: dict[str, Any]) -> dict[str, Any]:
    field = requirement.get("field")
    actual = get_path(candidate, field)
    expected = requirement.get("value")
    missing_policy = requirement.get("missing", "review")
    label = requirement.get("label") or requirement.get("id") or field

    if is_missing(actual):
        if missing_policy == "fail":
            status, credit = "fail", 0.0
        elif missing_policy == "pass":
            status, credit = "pass", 1.0
        elif missing_policy == "worst":
            status, credit = "unknown", 0.0
        else:
            status, credit = "unknown", None  # None -> unknown_credit
        reason = f"{field} 缺失，按 {missing_policy} 处理"
    elif expected is None:
        status, credit = "unknown", None
        reason = "需求未提供判定值"
    else:
        passed = _compare(actual, requirement.get("op", "contains_any"), expected)
        status, credit = ("pass", 1.0) if passed else ("fail", 0.0)
        reason = "" if passed else f"实际值 {str(actual)[:60]} 不满足 {requirement.get('op')} {str(expected)[:60]}"

    return {
        "id": requirement.get("id"),
        "label": label,
        "type": requirement.get("type", "should"),
        "field": field,
        "status": status,
        "credit": credit,
        "actual": actual,
        "expected": expected,
        "weight": requirement.get("weight"),
        "reason": reason,
    }


def evaluate_all(
    candidates: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    *,
    fit_threshold: float = 0.8,
    unknown_credit: float = 0.5,
) -> dict[str, Any]:
    for item in candidates:
        evaluation = evaluate_requirements(item, requirements, unknown_credit=unknown_credit)
        item["fit_details"] = evaluation["requirements"]
        item["fit_score"] = evaluation["fit_score"]
        item["fit_unmet"] = evaluation["unmet"]
        item["fit_unknown"] = evaluation["unknown"]
        item["must_passed"] = evaluation["must_passed"]
        item["must_unknown"] = evaluation["must_unknown"]
        if not item.get("eligible") or not evaluation["must_passed"]:
            item["fit_tier"] = "must_fail"
        elif evaluation["fit_score"] < fit_threshold:
            item["fit_tier"] = "below_fit"
        else:
            item["fit_tier"] = "pass"

    return {
        "fit_threshold": fit_threshold,
        "passed": sum(1 for item in candidates if item.get("fit_tier") == "pass"),
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

def evaluate_requirements(
    candidate: dict[str, Any],
    requirements: list[dict[str, Any]],
    *,
    unknown_credit: float = 0.5,
) -> dict[str, Any]:
    results = [_evaluate_one(candidate, req) for req in requirements if isinstance(req, dict)]
    musts = [item for item in results if item["type"] == "must"]
    shoulds = [item for item in results if item["type"] == "should"]

    must_failed = [item for item in musts if item["status"] == "fail"]
    must_unknown = [item for item in musts if item["status"] == "unknown"]

    total_weight = sum(float(item.get("weight") or 0) for item in shoulds)
    if total_weight > 0:
        earned = 0.0
        for item in shoulds:
            weight = float(item.get("weight") or 0)
            credit = item.get("credit")
            if credit is None:
                credit = unknown_credit if item["status"] == "unknown" else 0.0
            earned += weight * float(credit)
        fit_score = round(earned / total_weight, 4)
    else:
        fit_score = 1.0 if not must_failed else 0.0

    return {
        "requirements": results,
        "must_passed": not must_failed,
        "must_unknown": bool(must_unknown),
        "fit_score": fit_score,
        "unmet": [item["label"] for item in results if item["status"] == "fail"],
        "unknown": [item["label"] for item in results if item["status"] == "unknown"],
    }
