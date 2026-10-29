"""Render a one-page confirmation sheet for the user.

Nothing is collected before the user confirms this sheet: the requirements,
the exclusion rules and the dimension weights are the contract for the rest of
the run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

try:
    from .common import load_json, normalize_text
except ImportError:  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import load_json, normalize_text  # type: ignore


def _bullets(values: list[Any]) -> str:
    return "、".join(normalize_text(value) for value in values if normalize_text(value)) or "未指定"


def render_confirmation(brief: dict[str, Any], scorecard: dict[str, Any]) -> str:
    lines: list[str] = ["# 采购需求与评比维度确认单", ""]

    lines.append("## 需求")
    lines.append("")
    lines.append(f"- 品类：{normalize_text(brief.get('category')) or '未指定'}")
    lines.append(f"- 物品：{normalize_text(brief.get('item_description')) or '未指定'}")
    lines.append(f"- 数量：{brief.get('quantity', 1)}")
    budget = brief.get("budget_cny")
    per_unit = brief.get("budget_per_unit_cny")
    if per_unit is not None:
        lines.append(f"- 预算：单件 ¥{per_unit}，总额 ¥{budget if budget is not None else '未指定'}")
    else:
        lines.append(f"- 预算：{f'¥{budget}' if budget is not None else '未指定'}")
    lines.append(f"- 使用场景：{normalize_text(brief.get('usage_context')) or '未指定'}")
    lines.append("")

    requirements = brief.get("requirements") or []
    musts = [item for item in requirements if isinstance(item, dict) and item.get("type") == "must"]
    shoulds = [item for item in requirements if isinstance(item, dict) and item.get("type") == "should"]
    if requirements:
        lines.append("## 筛选（must）")
        lines.append("")
        for item in musts:
            lines.append(f"- {normalize_text(item.get('label') or item.get('id'))}")
        lines.append("")
        lines.append("## 需求匹配维度（should）")
        lines.append("")
        for item in shoulds:
            weight = item.get("weight")
            weight_text = f"（权重 {weight}）" if weight is not None else ""
            lines.append(f"- {normalize_text(item.get('label') or item.get('id'))}{weight_text}")
        lines.append("")

    hard_filters = scorecard.get("hard_filters") or []
    if hard_filters:
        lines.append("## 硬过滤")
        lines.append("")
        for item in hard_filters:
            lines.append(f"- {normalize_text(item.get('label') or item.get('id'))}：{normalize_text(item.get('reason'))}")
        lines.append("")

    groups: dict[str, list[dict[str, Any]]] = {"fit": [], "value": [], "risk": []}
    for dim in scorecard.get("dimensions") or []:
        if not isinstance(dim, dict):
            continue
        group = dim.get("group") if dim.get("group") in groups else "fit"
        groups[group].append(dim)

    lines.append("## 评比维度")
    lines.append("")
    for group, title in (("fit", "需求匹配"), ("value", "同价位性能"), ("risk", "风险")):
        if not groups[group]:
            continue
        total = sum(float(dim.get("weight") or 0) for dim in groups[group])
        lines.append(f"**{title}**（权重合计 {round(total, 2)}）")
        lines.append("")
        for dim in groups[group]:
            lines.append(
                f"- {normalize_text(dim.get('label') or dim.get('id'))}"
                f"：{dim.get('weight')}，{normalize_text(dim.get('method'))}"
            )
        lines.append("")

    stages = scorecard.get("stages") or {}
    if stages:
        lines.append(f"- 需求匹配阈值：{stages.get('fit_threshold', 0.8)}（低于阈值进入备选，不参与推荐）")
        lines.append("")

    assumptions = brief.get("assumptions") or []
    if assumptions:
        lines.append("## 假设（未确认）")
        lines.append("")
        for item in assumptions:
            lines.append(f"- {normalize_text(item)}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("确认这版，还是改哪一项？确认后才会开始采集。")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the requirement/dimension confirmation sheet")
    parser.add_argument("--brief", required=True)
    parser.add_argument("--scorecard", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    text = render_confirmation(load_json(args.brief), load_json(args.scorecard))
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"confirmation sheet written to {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
