"""Validate and freeze the requirement/scorecard plan.

Freezing records who confirmed the plan and at which revision.  The pipeline
refuses to run unconfirmed plans by default, so a report can always be traced
back to a plan the buyer actually saw.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

try:
    from .common import load_json, normalize_text, now_iso, save_json
except ImportError:  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import load_json, normalize_text, now_iso, save_json  # type: ignore


def validate_plan(brief: dict[str, Any], scorecard: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    requirements = brief.get("requirements") or []
    if not isinstance(requirements, list) or not requirements:
        errors.append("brief.requirements 不能为空：至少需要一条 must 或 should")

    must_ids = set()
    for item in requirements:
        if not isinstance(item, dict):
            errors.append("requirements 中存在非对象条目")
            continue
        if item.get("type") not in {"must", "should"}:
            errors.append(f"requirement {item.get('id')} 的 type 必须是 must 或 should")
        if not item.get("field"):
            errors.append(f"requirement {item.get('id')} 缺少 field")
        if item.get("type") == "should" and not item.get("weight"):
            errors.append(f"should requirement {item.get('id')} 缺少 weight")
        if item.get("type") == "must":
            must_ids.add(item.get("id"))

    dimensions = scorecard.get("dimensions") or []
    groups: dict[str, float] = {"fit": 0.0, "value": 0.0, "risk": 0.0}
    has_groups = any(isinstance(dim, dict) and dim.get("group") for dim in dimensions)
    if has_groups:
        for dim in dimensions:
            if not isinstance(dim, dict):
                continue
            group = dim.get("group")
            if group not in groups:
                errors.append(f"dimension {dim.get('id')} 的 group 必须是 fit/value/risk")
                continue
            groups[group] += float(dim.get("weight") or 0)
        for group, total in groups.items():
            if total and abs(total - 1.0) > 1e-6:
                errors.append(f"{group} 组权重合计为 {round(total, 4)}，必须为 1.0")
        stages = scorecard.get("stages")
        if not isinstance(stages, dict):
            errors.append("配置了 group 的评分卡必须包含 stages.fit_threshold")
        elif not (0 < float(stages.get("fit_threshold", 0)) <= 1):
            errors.append("stages.fit_threshold 必须在 (0, 1] 之间")
        if not any(isinstance(dim, dict) and dim.get("group") == "value" for dim in dimensions):
            errors.append("至少需要一个 value 维度用于同价位比较")
    else:
        errors.append("评分卡未分组：请为每个维度添加 group（fit/value/risk）并配置 stages")
    return errors


def freeze(
    brief_path: str | Path,
    scorecard_path: str | Path,
    *,
    by_user: bool = True,
    changes: list[str] | None = None,
) -> dict[str, Any]:
    brief = load_json(brief_path)
    scorecard = load_json(scorecard_path)
    errors = validate_plan(brief, scorecard)
    if errors:
        raise ValueError("plan invalid: " + "; ".join(errors))

    at = now_iso()
    changes = changes or []
    for payload in (brief, scorecard):
        previous = payload.get("confirmed") or {}
        revision = int(previous.get("revision") or 0) + 1
        payload["confirmed"] = {
            "by_user": bool(by_user),
            "at": at,
            "revision": revision,
            "changes": changes,
        }
    save_json(brief_path, brief)
    save_json(scorecard_path, scorecard)
    return {"brief": brief["confirmed"], "scorecard": scorecard["confirmed"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and freeze brief + scorecard")
    parser.add_argument("--brief", required=True)
    parser.add_argument("--scorecard", required=True)
    parser.add_argument("--by-user", action="store_true", default=True)
    parser.add_argument("--assumed", action="store_true", help="mark as assumed, not user-confirmed")
    args = parser.parse_args(argv)
    result = freeze(args.brief, args.scorecard, by_user=not args.assumed)
    print(f"frozen: revision={result['brief']['revision']} at={result['brief']['at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
