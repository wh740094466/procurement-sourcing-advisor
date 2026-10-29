"""Render a procurement memo as Markdown, CSV, and machine-readable JSON."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

try:
    from .common import UNKNOWN, get_path, is_missing, load_json, normalize_text, now_iso, round_money, save_json
    from .review_summary import summarize_reviews
    from .variant_matrix import build_variant_matrix
except ImportError:  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import (  # type: ignore
        UNKNOWN,
        get_path,
        is_missing,
        load_json,
        normalize_text,
        now_iso,
        round_money,
        save_json,
    )
    from review_summary import summarize_reviews  # type: ignore
    from variant_matrix import build_variant_matrix  # type: ignore


def _money(value: Any) -> str:
    number = round_money(value) if isinstance(value, (int, float)) else None
    return f"¥{number:,.2f}" if number is not None else "UNKNOWN"


def _cell(value: Any) -> str:
    text = normalize_text(value) or "UNKNOWN"
    return text.replace("|", "\\|").replace("\n", " ")


FIELD_LABELS = {
    "capacity": "容量",
    "ports": "接口",
    "cache": "缓存",
    "rpm": "转速",
    "model": "型号",
    "size": "尺寸",
    "condition": "成色",
    "recording": "记录技术",
    "screen_inch": "屏幕尺寸",
    "resolution": "分辨率",
    "panel": "面板",
    "ram_gb": "内存",
    "storage_gb": "硬盘",
    "cpu": "CPU",
    "weight_kg": "重量",
    "brand": "品牌",
    "drive_type": "硬盘类型",
    "noise_db": "噪音",
}


def _candidate_brief(candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    if not candidate:
        return None
    landed = candidate.get("landed_price") or {}
    tco = candidate.get("tco") or {}
    reviews = candidate.get("reviews") or {}
    return {
        "platform": candidate.get("platform"),
        "product_id": candidate.get("product_id"),
        "listing_id": candidate.get("listing_id"),
        "variant_id": candidate.get("variant_id"),
        "variant_label": candidate.get("variant_label"),
        "title": candidate.get("title"),
        "model": candidate.get("model"),
        "price": candidate.get("price"),
        "price_status": candidate.get("price_status"),
        "landed_price": landed.get("landed_price"),
        "landed_floor": landed.get("floor"),
        "landed_status": landed.get("status"),
        "tco_exact": tco.get("exact_total"),
        "tco_floor": tco.get("floor"),
        "tco_status": tco.get("status"),
        "score": candidate.get("score"),
        "score_confidence": candidate.get("score_confidence"),
        "fit_score": candidate.get("fit_score"),
        "fit_tier": candidate.get("fit_tier"),
        "fit_unmet": candidate.get("fit_unmet", []),
        "performance": candidate.get("performance"),
        "value_score": candidate.get("value_score"),
        "price_index": candidate.get("price_index"),
        "on_frontier": candidate.get("on_frontier"),
        "dominated_by": candidate.get("dominated_by", []),
        "rank": candidate.get("rank"),
        "url": candidate.get("url"),
        "shop_name": candidate.get("shop_name"),
        "reviews_status": reviews.get("status"),
        "rating": reviews.get("rating"),
        "rating_count": reviews.get("rating_count"),
        "review_sample_size": reviews.get("sample_size"),
        "negative_rate": reviews.get("negative_rate"),
        "missing_fields": candidate.get("missing_fields", []),
        "needs_review": candidate.get("needs_review", False),
    }


def _top_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [
            item
            for item in candidates
            if item.get("eligible") and item.get("fit_tier") in (None, "pass")
        ],
        key=lambda item: int(item.get("rank") or 10**9),
    )


def build_report(
    brief: dict[str, Any],
    candidates: list[dict[str, Any]],
    scorecard: dict[str, Any],
    bundle: dict[str, Any] | None = None,
    *,
    warnings: list[str] | None = None,
    errors: list[dict[str, Any]] | None = None,
    methodology: dict[str, Any] | None = None,
    price_anchors: dict[str, Any] | None = None,
    fit_summary: dict[str, Any] | None = None,
    value_summary: dict[str, Any] | None = None,
    report_id: str | None = None,
) -> dict[str, Any]:
    bundle = bundle or {}
    warnings = warnings or []
    errors = errors or []
    present_platforms = {c.get("platform") for c in candidates}
    for code, label in (("taobao", "淘宝"), ("jd", "京东")):
        if code not in present_platforms:
            warnings.append(f"本次未采集到{label}数据（未检索/登录墙/访问异常），结论仅基于已采集平台")
    top = _top_candidates(candidates)
    primary = top[0] if top else None
    alternatives = top[1:3]
    below_fit = [item for item in candidates if item.get("fit_tier") == "below_fit"]
    eliminated = [item for item in candidates if not item.get("eligible")]
    for item in eliminated:
        item.setdefault("elimination_reasons", [])
        if not item["elimination_reasons"]:
            item["elimination_reasons"] = [
                result.get("reason", "未通过硬过滤")
                for result in item.get("hard_filter_results", [])
                if result.get("status") == "fail"
            ]

    evidence_gaps: list[dict[str, Any]] = []
    for candidate in top[:5]:
        for field in candidate.get("missing_fields", []) or []:
            evidence_gaps.append(
                {
                    "platform": candidate.get("platform"),
                    "product_id": candidate.get("product_id"),
                    "field": field,
                    "impact": "降低置信度；影响价格/售后/库存判断",
                    "next_action": _next_action_for_field(field),
                }
            )
        for dim_id in candidate.get("missing_dimensions", []) or []:
            evidence_gaps.append(
                {
                    "platform": candidate.get("platform"),
                    "product_id": candidate.get("product_id"),
                    "field": f"score_dimension:{dim_id}",
                    "impact": "该评分维度按缺失策略计分",
                    "next_action": "补充商品页/客服证据，或调整评分卡缺失策略",
                }
            )

    actions: set[str] = set()
    for candidate in candidates:
        if candidate.get("action_required"):
            actions.add(candidate["action_required"])
        status = candidate.get("enrichment_status")
        if status in {"login_required", "captcha"}:
            actions.add("浏览器补证遇到登录墙/验证码，需先完成登录")
        elif status == "blocked":
            actions.add(
                "浏览器补证被平台拒绝（页面未返回商品内容），未绕过；以 MCP 数据为准，需人工打开页面核对"
            )
    needs_user_action = sorted(actions)
    if (price_anchors or {}).get("anomalies"):
        needs_user_action.append(
            f"{len(price_anchors['anomalies'])} 条候选价格显著低于官方/自营锚点，已限制价格得分；下单前需证实成色、发票与保修主体"
        )
        needs_user_action = sorted(set(needs_user_action))
    if any(candidate.get("price_status") != "visible" for candidate in candidates):
        needs_user_action.append("存在价格隐藏/缺失的候选，不能按确定价格比较")

    key_risks: list[str] = []
    if primary:
        if primary.get("price_status") != "visible":
            key_risks.append("推荐候选价格不是可见确定值")
        if primary.get("low_confidence"):
            key_risks.append("推荐候选证据完整度偏低")
        if (primary.get("reviews") or {}).get("status") != "ok":
            key_risks.append("推荐候选缺少评价证据，无法评估集中故障风险")
        if any(field in {"warranty", "invoice", "stock"} for field in primary.get("missing_fields", [])):
            labels = [
                {"warranty": "保修", "invoice": "发票", "stock": "库存"}[field]
                for field in ("warranty", "invoice", "stock")
                if field in primary.get("missing_fields", [])
            ]
            key_risks.append("推荐候选的" + "、".join(labels) + "未确认")
        if primary.get("needs_review"):
            key_risks.append("推荐候选存在需人工复核的筛选或评分项")
    if not primary:
        key_risks.append("没有通过硬过滤的候选，无法给出推荐")
    if bundle.get("warnings"):
        key_risks.extend(bundle["warnings"])

    variant_matrix = build_variant_matrix(candidates)
    review_digest = {
        f"{item.get('platform')}:{item.get('product_id')}": summarize_reviews(item.get("reviews"))
        for item in top[:5]
    }
    for candidate in top[:5]:
        if (candidate.get("reviews") or {}).get("status") != "ok":
            evidence_gaps.append(
                {
                    "platform": candidate.get("platform"),
                    "product_id": candidate.get("product_id"),
                    "field": "reviews",
                    "impact": "无法评估口碑与集中质量风险",
                    "next_action": "在商品页评价区采集评分、评价数与首屏负评样本（保留原话 snippet）",
                }
            )

    visible_candidates = [c for c in candidates if c.get("price_status") == "visible"]
    hidden_candidates = [c for c in candidates if c.get("price_status") != "visible"]
    price_scope = {
        "total": len(candidates),
        "visible": len(visible_candidates),
        "hidden_or_missing": len(hidden_candidates),
        "hidden_ids": [
            f"{c.get('platform')}:{c.get('product_id')}" for c in hidden_candidates
        ],
    }

    safe_option = None
    if primary is not None and not any(
        marker in normalize_text(primary.get("shop_name")) for marker in ("自营", "官方旗舰", "官方直营")
    ):
        safe_option = next(
            (
                item
                for item in top[1:]
                if "自营" in normalize_text(item.get("shop_name"))
                and item.get("price") is not None
                and (item.get("price") or 0) > (primary.get("price") or 0)
            ),
            None,
        )

    summary = {
        "quantity": brief.get("quantity", 1),
        "budget_cny": brief.get("budget_cny"),
        "budget_per_unit_cny": brief.get("budget_per_unit_cny"),
        "price_scope": price_scope,
        "safe_option": _candidate_brief(safe_option) if safe_option else None,
        "recommendation": _candidate_brief(primary),
        "alternatives": [_candidate_brief(item) for item in alternatives],
        "below_fit": [_candidate_brief(item) for item in below_fit],
        "fit": fit_summary,
        "value": value_summary,
        "decision_confidence": primary.get("score_confidence") if primary else None,
        "key_risks": list(dict.fromkeys(key_risks)),
        "needs_user_action": list(dict.fromkeys(needs_user_action)),
        "eligible_count": len(top),
        "eliminated_count": len(eliminated),
    }

    report = {
        "report_id": report_id or f"procurement-{normalize_text(brief.get('category') or 'general')}",
        "generated_at": now_iso(),
        "brief": brief,
        "scope": {
            "platforms": ["taobao", "jd"],
            "excluded_platforms": ["pdd"],
            "price_note": "价格来自淘宝/京东检索、详情或浏览器补证；未含账号券、运费、税费时不能视为最终到手价。拼多多不在覆盖范围。",
            "browser_capture": "由 AI 通过 playwright-mcp 打开页面并按配置采集；遇到登录墙/验证码/内容未返回时停止。",
        },
        "summary": summary,
        "scorecard": scorecard,
        "candidates": candidates,
        "variant_matrix": variant_matrix,
        "review_summary": review_digest,
        "price_anchors": price_anchors or {},
        "fit_summary": fit_summary,
        "value_summary": value_summary,
        "eliminated": eliminated,
        "bundle": bundle,
        "evidence_gaps": evidence_gaps,
        "methodology": methodology
        or {
            "stages": [
                "需求形式化",
                "淘宝/京东多路召回（京东串行）",
                "详情补全与浏览器补证",
                "归一化与价格状态标记",
                "到手价/TCO",
                "硬过滤与加权评分",
                "拆单/凑单评估",
                "报告与自检",
            ],
            "missing_policy": "缺失字段保留 UNKNOWN；评分按评分卡缺失策略处理；置信度独立展示。",
            "scorecard_note": "评分项由 AI 根据实际物品生成，经 JSON 校验后由脚本确定性计算。",
        },
        "warnings": warnings,
        "errors": errors,
    }
    return report


def _next_action_for_field(field: str) -> str:
    return {
        "price": "打开商品链接/结算页核对当前价格；不要用 0 代替",
        "specs": "查看商品详情规格表，必要时浏览器补证",
        "warranty": "查看服务说明或咨询客服，记录原话/截图",
        "invoice": "确认是否可开票、票种与税率",
        "stock": "确认现货/发货时间，避免按页面默认假设",
        "shop": "核对店铺主体与自营/旗舰店标识",
        "sales": "销量缺失不作为质量证据",
    }.get(field, "补充证据或标注 UNKNOWN")


def render_markdown(report: dict[str, Any]) -> str:
    brief = report.get("brief", {})
    summary = report.get("summary", {})
    primary = summary.get("recommendation")
    alternatives = summary.get("alternatives", [])
    candidates = report.get("candidates", [])
    eliminated = report.get("eliminated", [])
    bundle = report.get("bundle", {})
    lines: list[str] = []
    lines.append(f"# 采购方案：{_cell(brief.get('item_description') or brief.get('category') or '未命名采购')}")
    lines.append("")
    present = [p for p in ("taobao", "jd") if any(c.get("platform") == p for c in candidates)]
    scope_labels = {"taobao": "淘宝", "jd": "京东"}
    scope_text = "、".join(scope_labels[p] for p in present) or "无可用平台数据"
    lines.append(
        f"> **比价范围**：{scope_text}；**拼多多未覆盖**。价格是选中该配置后页面显示的价格，"
        "未含账号券、运费、税费，不能直接视为最终到手价。"
    )
    lines.append("")

    lines.append("## 1. 决策摘要")
    lines.append("")
    if primary:
        lines.append(
            f"- **推荐**：{_cell(primary.get('title'))}（配置：{_cell(primary.get('variant_label'))}；"
            f"{primary.get('platform')} / {primary.get('product_id')}）"
        )
        if primary.get("fit_score") is not None:
            lines.append(
                f"- **需求满足度**：{primary.get('fit_score')}；性价比 value：{primary.get('value_score')}；证据置信度 {primary.get('score_confidence', 'UNKNOWN')}"
            )
        else:
            lines.append(
                f"- **评分**：{primary.get('score', 'UNKNOWN')} / 100；证据置信度 {primary.get('score_confidence', 'UNKNOWN')}"
            )
        lines.append(f"- **页面价**：{_money(primary.get('price'))}；到手价状态：{primary.get('landed_status') or 'UNKNOWN'}")
        lines.append(f"- **链接**：{primary.get('url') or 'UNKNOWN'}")
        lines.append(f"- **数量**：{summary.get('quantity', 1)}")
        if summary.get("budget_per_unit_cny") is not None:
            lines.append(f"- **单件预算**：{_money(summary.get('budget_per_unit_cny'))}")
        elif summary.get("budget_cny") is not None:
            lines.append(f"- **总预算**：{_money(summary.get('budget_cny'))}")
        scope = summary.get("price_scope") or {}
        if scope.get("hidden_or_missing"):
            lines.append(
                "- **价格可比范围**："
                f"{scope.get('visible')}/{scope.get('total')} 条候选价格可见；"
                f"{scope.get('hidden_or_missing')} 条价格隐藏/缺失，未参与价格排序，"
                "结论只覆盖价格可见的候选，不能据此宣称全网最低。"
            )
        safe_option = summary.get("safe_option")
        if safe_option:
            gap = (safe_option.get("price") or 0) - (primary.get("price") or 0)
            lines.append(
                f"- **稳妥选项**：{_cell(safe_option.get('title'))}"
                f"（{_cell(safe_option.get('variant_label'))}；{_money(safe_option.get('price'))}），"
                f"比推荐贵 {_money(gap)}；自营/官方渠道，适合不愿承担第三方店保风险的场景。"
            )
    else:
        lines.append("- **没有通过硬过滤的候选**，请放宽条件或补充数据后重跑。")
    if summary.get("key_risks"):
        lines.append("- **主要风险**：")
        for risk in summary["key_risks"]:
            lines.append(f"  - {risk}")
    if summary.get("needs_user_action"):
        lines.append("- **需要人工确认/操作**：")
        for action in summary["needs_user_action"]:
            lines.append(f"  - {action}")
    lines.append("")

    lines.append("## 2. 采购简报")
    lines.append("")
    lines.append(f"- 品类：{_cell(brief.get('category'))}")
    lines.append(f"- 物品：{_cell(brief.get('item_description'))}")
    lines.append(f"- 数量：{brief.get('quantity', 1)}")
    lines.append(f"- 预算：{_money(brief.get('budget_cny'))}")
    lines.append(f"- 必选条件：{_cell('、'.join(brief.get('must_have', []) or ['未指定']))}")
    lines.append(f"- 加分条件：{_cell('、'.join(brief.get('nice_to_have', []) or ['未指定']))}")
    lines.append(f"- 排除条件：{_cell('、'.join(brief.get('exclusions', []) or ['未指定']))}")
    lines.append(f"- 是否允许拆单：{'是' if brief.get('allow_split') else '否/未确认'}")
    lines.append(f"- 是否允许替代型号：{'是' if brief.get('allow_alternative_models') else '否/未确认'}")
    lines.append(f"- 使用场景：{_cell(brief.get('usage_context'))}")
    confirmed = brief.get("confirmed") or {}
    if confirmed:
        status_text = "用户已确认" if confirmed.get("by_user") else "假设（未确认）"
        lines.append(
            f"- 确认记录：第 {confirmed.get('revision', 1)} 版，{confirmed.get('at', '')}，{status_text}"
        )
    lines.append("")

    lines.append("## 3. 候选对比")
    lines.append("")
    staged_columns = any(item.get("fit_score") is not None for item in candidates)
    if staged_columns:
        lines.append(
            "| 排名 | 平台 | 配置 | 商品 | 页面价 | 到手价状态 | TCO状态 | 需求满足 | 性价比 | 置信度 | 店铺 | 链接 |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    else:
        lines.append(
            "| 排名 | 平台 | 配置 | 商品 | 页面价 | 到手价状态 | TCO状态 | 评分 | 置信度 | 店铺 | 链接 |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for item in sorted(candidates, key=lambda row: int(row.get("rank") or 10**9)):
        if not item.get("eligible"):
            continue
        landed = item.get("landed_price") or {}
        tco = item.get("tco") or {}
        rank_value = item.get("rank") if item.get("rank") is not None else "—"
        if staged_columns:
            lines.append(
                "| {rank} | {platform} | {variant} | {title} | {price} | {landed} | {tco} | {fit} | {value} | {conf} | {shop} | {url} |".format(
                    rank=rank_value,
                    platform=item.get("platform"),
                    variant=_cell(item.get("variant_label")),
                    title=_cell(item.get("title")),
                    price=_money(item.get("price")) + (" ⚠" if item.get("price_anomaly") else ""),
                    landed=landed.get("status") or "UNKNOWN",
                    tco=tco.get("status") or "UNKNOWN",
                    fit=item.get("fit_score") if item.get("fit_score") is not None else "—",
                    value=item.get("value_score") if item.get("value_score") is not None else "—",
                    conf=item.get("score_confidence"),
                    shop=_cell(item.get("shop_name")),
                    url=item.get("url") or "",
                )
            )
        else:
            lines.append(
                "| {rank} | {platform} | {variant} | {title} | {price} | {landed} | {tco} | {score} | {conf} | {shop} | {url} |".format(
                    rank=rank_value,
                    platform=item.get("platform"),
                    variant=_cell(item.get("variant_label")),
                    title=_cell(item.get("title")),
                    price=_money(item.get("price")) + (" ⚠" if item.get("price_anomaly") else ""),
                    landed=landed.get("status") or "UNKNOWN",
                    tco=tco.get("status") or "UNKNOWN",
                    score=item.get("score"),
                    conf=item.get("score_confidence"),
                    shop=_cell(item.get("shop_name")),
                    url=item.get("url") or "",
                )
            )
    if not any(item.get("eligible") for item in candidates):
        lines.append("| - | - | - | 无通过硬过滤的候选 | - | - | - | - | - | - | - |")
    lines.append("")

    matrices = report.get("variant_matrix") or []
    if matrices:
        lines.append("### 3.1 同链接配置对比")
        lines.append("")
        lines.append("同一链接下的不同配置单独计价、单独评分；价差来自配置差异，不能合并成一个价格。")
        lines.append("")
        for matrix in matrices[:5]:
            lines.append(f"**{matrix.get('platform')} / {matrix.get('listing_id')}**：{_cell(matrix.get('title'))}")
            lines.append("")
            differing = matrix.get("differing_attributes") or []
            headers = ["配置", "价格", "库存", "fit", "value", "状态"] + differing
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("|" + "---|" * len(headers))
            for row in matrix.get("variants") or []:
                cells = [
                    _cell(row.get("variant_label")),
                    _money(row.get("price")),
                    _cell(row.get("stock_status")),
                    str(row.get("fit_score") if row.get("fit_score") is not None else "—"),
                    str(row.get("value_score") if row.get("value_score") is not None else "—"),
                    row.get("status") or "—",
                ]
                cells.extend(_cell((row.get("differences") or {}).get(key)) for key in differing)
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")

    anchor_data = report.get("price_anchors") or {}
    anchor_rows = anchor_data.get("anchors") or []
    anomalies = anchor_data.get("anomalies") or []
    if anchor_rows:
        lines.append("### 3.2 价格锚点与异常低价")
        lines.append("")
        lines.append(
            "锚点取同型号下自营/官方旗舰店价格的中位数。低于锚点 "
            f"{float(anchor_data.get('min_ratio', 0.7)):.0%} 的候选，价格分封顶并标记 ⚠，"
            "需先证实成色、发票与保修主体，不能直接当作同款低价。"
        )
        lines.append("")
        lines.append("| 型号 | 官方/自营锚点 | 来源店铺 | 样本数 |")
        lines.append("|---|---|---|---|")
        for anchor in anchor_rows:
            models = "、".join(anchor.get("models") or []) or anchor.get("group")
            lines.append(
                "| {model} | {price} | {shop} | {n} |".format(
                    model=_cell(models),
                    price=_money(anchor.get("anchor_price")),
                    shop=_cell(anchor.get("source_shop")),
                    n=anchor.get("sample_size"),
                )
            )
        lines.append("")
        if any(int(anchor.get("sample_size") or 0) < 2 for anchor in anchor_rows):
            lines.append("样本数为 1 时，锚点就是该自营/官方店的当前售价，只作参照，不代表全网底价。")
            lines.append("")
        if anomalies:
            lines.append("明显低于锚点的候选：")
            lines.append("")
            for item in anomalies:
                ratio = item.get("ratio")
                ratio_text = f"{float(ratio):.0%}" if ratio is not None else "UNKNOWN"
                lines.append(
                    f"- {item.get('platform')}:{item.get('variant_id')}（{_cell(item.get('shop'))}）"
                    f" {_money(item.get('price'))}，约为锚点的 {ratio_text}"
                )
            lines.append("")

    fit_candidates = [item for item in candidates if item.get("fit_details")]
    if fit_candidates:
        fit_candidates.sort(
            key=lambda item: (
                item.get("fit_tier") != "pass",
                int(item.get("rank") or 10**9),
            )
        )
        shown = fit_candidates[:8]
        labels = [entry.get("label") for entry in (shown[0].get("fit_details") or [])]
        lines.append("### 3.3 需求匹配矩阵")
        lines.append("")
        lines.append("✓ 满足 / ✗ 不满足 / ? 未确认；fit 为 should 维度的加权满足度，低于阈值不参与推荐。")
        lines.append("")
        lines.append("| 候选 | " + " | ".join(_cell(label) for label in labels) + " | fit | 未满足 |")
        lines.append("|" + "---|" * (len(labels) + 3))
        marks = {"pass": "✓", "fail": "✗", "unknown": "?"}
        for item in shown:
            details = {entry.get("label"): entry for entry in item.get("fit_details") or []}
            cells = [marks.get((details.get(label) or {}).get("status"), "?") for label in labels]
            fails = [entry for entry in (item.get("fit_details") or []) if entry.get("status") == "fail"]
            unknowns = [entry for entry in (item.get("fit_details") or []) if entry.get("status") == "unknown"]
            unmet_parts = [entry.get("reason") or entry.get("label") for entry in fails]
            if unknowns:
                unmet_parts.append("未确认：" + "、".join(entry.get("label") or "" for entry in unknowns))
            unmet = "；".join(part for part in unmet_parts if part) or "—"
            lines.append(
                "| {name} | {cells} | {fit} | {unmet} |".format(
                    name=_cell(item.get("variant_label") or item.get("product_id")),
                    cells=" | ".join(cells),
                    fit=item.get("fit_score"),
                    unmet=_cell(unmet),
                )
            )
        lines.append("")

    value = report.get("value_summary") or {}
    value_rows = [
        item
        for item in candidates
        if item.get("fit_tier") == "pass" and item.get("value_score") is not None
    ]
    value_rows.sort(key=lambda item: -float(item.get("value_score") or 0))
    if value_rows:
        lines.append("### 3.4 性价比与帕累托前沿")
        lines.append("")
        lines.append(
            f"性能 = value 维度加权分（0–5）；价格指数 = 页面价 / 官方锚点；value = 性能 / 价格指数。"
            f"前沿候选 {len(value.get('frontier') or [])} 个，被支配 {len(value.get('dominated') or [])} 个。"
        )
        lines.append("")
        lines.append("| 候选 | 价格 | 性能 | 价格指数 | value | 位置 |")
        lines.append("|---|---|---|---|---|---|")
        for item in value_rows[:8]:
            if item.get("value_note"):
                position = "未证实（不参与支配）"
            else:
                position = "前沿" if item.get("on_frontier") else "被支配"
                dominated = "、".join(item.get("dominated_by") or [])
                if dominated:
                    position += f"（{_cell(dominated)}）"
            lines.append(
                "| {name} | {price} | {perf} | {index} | {value} | {pos} |".format(
                    name=_cell(item.get("variant_label") or item.get("product_id")),
                    price=_money(item.get("price")),
                    perf=item.get("performance"),
                    index=item.get("price_index"),
                    value=item.get("value_score"),
                    pos=position,
                )
            )
        lines.append("")

    field_keys: list[str] = []
    for dim in (report.get("scorecard") or {}).get("dimensions") or []:
        field = normalize_text(dim.get("field"))
        if field.startswith("attributes."):
            field_keys.append(field.split(".", 1)[1])
    for requirement in brief.get("requirements") or []:
        field = normalize_text(requirement.get("field"))
        if field.startswith("attributes."):
            field_keys.append(field.split(".", 1)[1])
    field_keys = list(dict.fromkeys(field_keys))[:8]
    field_candidates = [
        item
        for item in candidates
        if item.get("fit_details") and (item.get("rank") is not None or item.get("fit_tier") == "below_fit")
    ]
    field_candidates.sort(key=lambda item: (item.get("fit_tier") != "pass", int(item.get("rank") or 10**9)))
    if field_keys and field_candidates:
        lines.append("### 3.5 评分字段取值")
        lines.append("")
        lines.append("以下是评分与需求判定实际读取的字段值；`UNKNOWN` 表示页面未提供，不是 0。")
        lines.append("")
        headers = ["候选"] + [FIELD_LABELS.get(key, key) for key in field_keys] + ["保修", "发票", "库存"]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "---|" * len(headers))
        for item in field_candidates[:8]:
            attributes = item.get("attributes") or {}
            detail = item.get("detail") or {}
            cells = [_cell(item.get("variant_label") or item.get("product_id"))]
            cells.extend(_cell(attributes.get(key)) for key in field_keys)
            cells.extend(
                [
                    _cell(detail.get("warranty")),
                    _cell(detail.get("invoice")),
                    _cell(detail.get("stock_status")),
                ]
            )
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

    lines.append("## 4. 推荐方案")
    lines.append("")
    if primary:
        lines.append(
            f"推荐 **{primary.get('title')}**（配置：{_cell(primary.get('variant_label'))}；"
            f"{primary.get('platform')}，ID {primary.get('product_id')}）。"
        )
        lines.append("")
        lines.append("推荐理由：")
        if primary.get("fit_score") is None:
            lines.append(f"- 综合评分排名第 {primary.get('rank')}，得分 {primary.get('score')}。")
        if primary.get("fit_score") is not None:
            unmet = "、".join(primary.get("fit_unmet") or []) or "无"
            lines.append(
                f"- 需求满足度：{primary.get('fit_score')}（阈值 {((report.get('fit_summary') or {}).get('fit_threshold'))}）；未满足：{_cell(unmet)}。"
            )
        if primary.get("value_score") is not None:
            position = "帕累托前沿" if primary.get("on_frontier") else "非前沿"
            lines.append(
                f"- 性价比：value {primary.get('value_score')}（性能 {primary.get('performance')} / 价格指数 {primary.get('price_index')}，{position}）。"
            )
        lines.append(f"- 到手价状态：{primary.get('landed_status') or 'UNKNOWN'}；TCO 状态：{primary.get('tco_status') or 'UNKNOWN'}。")
        lines.append(f"- 证据置信度：{primary.get('score_confidence')}；缺失字段：{_cell('、'.join(primary.get('missing_fields', [])) or '无')}。")
        lines.append("")
        breakdown = primary.get("score_breakdown") or []
        if not breakdown:
            full = next(
                (
                    item
                    for item in candidates
                    if item.get("platform") == primary.get("platform")
                    and str(item.get("product_id")) == str(primary.get("product_id"))
                ),
                None,
            )
            breakdown = (full or {}).get("score_breakdown") or []
        if breakdown:
            policies = {
                dim.get("id"): dim.get("missing")
                for dim in (report.get("scorecard", {}).get("dimensions") or [])
                if isinstance(dim, dict)
            }
            lines.append("评分明细（0–5 分折算为 0–100 加权分）：")
            lines.append("")
            lines.append("| 维度 | 权重 | 得分 | 加权分 | 取值 | 说明 |")
            lines.append("|---|---|---|---|---|---|")
            for item in breakdown:
                known = item.get("known")
                note = item.get("evidence") or ""
                if not known:
                    note = f"缺失，按 {policies.get(item.get('dimension_id'), '策略')} 计分"
                raw_text = str(item.get("raw_value") if known else "UNKNOWN")
                if len(raw_text) > 60:
                    raw_text = raw_text[:57] + "…"
                lines.append(
                    "| {label} | {weight} | {score} | {points} | {raw} | {note} |".format(
                        label=_cell(item.get("label") or item.get("dimension_id")),
                        weight=item.get("weight"),
                        score=item.get("normalized_score"),
                        points=item.get("weighted_points"),
                        raw=_cell(raw_text),
                        note=_cell(note),
                    )
                )
            lines.append("")
        digest = (report.get("review_summary") or {}).get(
            f"{primary.get('platform')}:{primary.get('product_id')}"
        )
        if digest:
            lines.append("### 4.1 评价摘要")
            lines.append("")
            lines.append(f"- {digest.get('headline')}")
            for theme in digest.get("top_negative") or []:
                snippet = (theme.get("snippets") or [""])[0]
                if snippet:
                    lines.append(f"  - {theme.get('theme')}（{theme.get('count')} 次）：{_cell(snippet)}")
            lines.append("")

        lines.append("下单前必须复核：当前售价、账号券、运费、税费、库存/发货时间、发票与保修。")
    else:
        lines.append("当前没有可推荐候选。请检查预算、必选条件或平台登录态后重试。")
    lines.append("")

    lines.append("## 5. 备选方案")
    lines.append("")
    if alternatives:
        for index, item in enumerate(alternatives, start=1):
            lines.append(
                f"{index}. {_cell(item.get('title'))}（{item.get('platform')} / {item.get('product_id')}）"
                f"；评分 {item.get('score')}；置信度 {item.get('score_confidence')}；{item.get('url')}"
            )
    else:
        lines.append("无通过硬过滤的备选；不要用未通过硬过滤的候选凑数。")
    lines.append("")

    lines.append("## 6. 淘汰与风险")
    lines.append("")
    if eliminated:
        for item in eliminated[:15]:
            reasons = "；".join(item.get("elimination_reasons", []) or ["未通过硬过滤"])
            lines.append(f"- {_cell(item.get('title'))}（{item.get('platform')} / {item.get('product_id')}）：{reasons}")
        if len(eliminated) > 15:
            lines.append(f"- 其余 {len(eliminated) - 15} 条见 JSON 报告。")
    else:
        lines.append("- 无淘汰候选。")
    lines.append("")

    lines.append("## 7. 拆单/凑单")
    lines.append("")
    if bundle.get("recommended_option"):
        lines.append(f"- 建议：**{bundle.get('recommended_option')}**")
        lines.append(f"- 单渠道总价：{_money((bundle.get('single_order') or {}).get('total'))}")
        lines.append(f"- 拆单总价：{_money((bundle.get('split_order') or {}).get('total'))}")
        lines.append(f"- 节省：{_money(bundle.get('savings')) if bundle.get('savings') is not None else 'UNKNOWN'}（{bundle.get('savings_status')}）")
        for warning in bundle.get("warnings", []) or []:
            lines.append(f"- 注意：{warning}")
    else:
        lines.append("- 未生成拆单方案；原因见 JSON 的 bundle 字段。")
    lines.append("")

    lines.append("## 8. 证据缺口与下一步")
    lines.append("")
    if report.get("evidence_gaps"):
        for gap in report["evidence_gaps"][:30]:
            lines.append(
                f"- {gap.get('platform')}:{gap.get('product_id')} / {gap.get('field')}：{gap.get('impact')}；下一步：{gap.get('next_action')}"
            )
    else:
        lines.append("- 主要字段未发现缺口；仍建议下单前复核实时价格与库存。")
    lines.append("")

    lines.append("## 9. 方法与范围")
    lines.append("")
    lines.append("- 采集：AI 通过 playwright-mcp 打开淘宝/京东页面，逐 listing 展开配置，逐配置读价。")
    lines.append("- 粒度：一个链接 ≠ 一个商品；每个配置（SKU）是独立候选，价格与参数按当前选中配置记录。")
    lines.append("- 评价：仅记录评价区可见的评分、评价数与首屏样本；样本量写入报告，不做统计推断。")
    lines.append("- 归一化：保留 `UNKNOWN`；价格隐藏/为空不得当作 0。")
    lines.append("- 评分：AI 按实际物品生成评分卡，脚本校验权重和为 1 后确定性计算；置信度独立于得分。")
    lines.append("- 价格：页面价不含账号券、运费、税费；未提供时输出下限/区间。")
    lines.append("")

    lines.append("## 10. 证据附录")
    lines.append("")
    for item in sorted(candidates, key=lambda row: int(row.get("rank") or 10**9))[:10]:
        lines.append(
            f"### {_cell(item.get('title'))}（{item.get('platform')} / {item.get('variant_id') or item.get('product_id')}）"
        )
        lines.append(f"- URL：{item.get('url') or 'UNKNOWN'}")
        lines.append(f"- 配置：{_cell(item.get('variant_label'))}；选项链：{_cell(' > '.join(item.get('option_path', []) or []))}")
        lines.append(f"- 价格状态：{item.get('price_status')}；价格说明：{item.get('price_note')}")
        lines.append(f"- 店铺：{item.get('shop_name')}；销量：{item.get('sales')}")
        lines.append(f"- 缺失字段：{_cell('、'.join(item.get('missing_fields', [])) or '无')}")
        detail = item.get("detail") or {}
        if detail.get("after_sales"):
            lines.append(f"- 售后保障：{_cell(detail.get('after_sales'))}")
        if detail.get("usage_notes"):
            lines.append(f"- 使用说明：{_cell(detail.get('usage_notes'))}")
        for evidence in (item.get("evidence") or [])[:4]:
            lines.append(
                f"- 证据（{evidence.get('field')}）：\"{_cell(evidence.get('snippet'))}\" — {evidence.get('source_url')}"
            )
        reviews = item.get("reviews") or {}
        if reviews.get("status") == "ok":
            lines.append(
                f"- 评价：评分 {reviews.get('rating')}，评价数 {reviews.get('rating_count')}，样本 {reviews.get('sample_size')}"
            )
        lines.append("")

    if report.get("warnings"):
        lines.append("## 警告")
        lines.append("")
        for warning in report["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")
    if report.get("errors"):
        lines.append("## 数据错误")
        lines.append("")
        for error in report["errors"]:
            lines.append(f"- {error.get('code', 'ERROR')}: {error.get('message', '')}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


CSV_COLUMNS = [    "rank",
    "platform",
    "listing_id",
    "variant_id",
    "variant_label",
    "option_path",
    "title",
    "model",
    "price",
    "price_status",
    "price_anchor",
    "price_ratio",
    "price_anomaly",
    "landed_status",
    "landed_price",
    "landed_floor",
    "tco_status",
    "tco_exact",
    "tco_floor",
    "score",
    "score_confidence",
    "fit_score",
    "fit_tier",
    "performance",
    "value_score",
    "eligible",
    "shop_name",
    "sales",
    "key_specs",
    "rating",
    "rating_count",
    "review_sample_size",
    "negative_rate",
    "missing_fields",
    "url",
    "elimination_reasons",
]


def write_csv(report: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for item in sorted(report.get("candidates", []), key=lambda row: int(row.get("rank") or 10**9)):
            landed = item.get("landed_price") or {}
            tco = item.get("tco") or {}
            reviews = item.get("reviews") or {}
            writer.writerow(
                {
                    "rank": item.get("rank") or "",
                    "platform": item.get("platform") or "",
                    "listing_id": item.get("listing_id") or "",
                    "variant_id": item.get("variant_id") or item.get("product_id") or "",
                    "variant_label": item.get("variant_label") or "",
                    "option_path": " > ".join(item.get("option_path", []) or []),
                    "title": item.get("title") or "",
                    "model": item.get("model") or "",
                    "price": item.get("price") if item.get("price") is not None else "",
                    "price_status": item.get("price_status") or "",
                    "price_anchor": (item.get("price_anchor") or {}).get("value") or "",
                    "price_ratio": item.get("price_ratio") if item.get("price_ratio") is not None else "",
                    "price_anomaly": "true" if item.get("price_anomaly") else "false",
                    "landed_status": landed.get("status") or "",
                    "landed_price": landed.get("landed_price") if landed.get("landed_price") is not None else "",
                    "landed_floor": landed.get("floor") if landed.get("floor") is not None else "",
                    "tco_status": tco.get("status") or "",
                    "tco_exact": tco.get("exact_total") if tco.get("exact_total") is not None else "",
                    "tco_floor": tco.get("floor") if tco.get("floor") is not None else "",
                    "score": item.get("score") if item.get("score") is not None else "",
                    "score_confidence": item.get("score_confidence") if item.get("score_confidence") is not None else "",
                    "fit_score": item.get("fit_score") if item.get("fit_score") is not None else "",
                    "fit_tier": item.get("fit_tier") or "",
                    "performance": item.get("performance") if item.get("performance") is not None else "",
                    "value_score": item.get("value_score") if item.get("value_score") is not None else "",
                    "eligible": "true" if item.get("eligible") else "false",
                    "shop_name": item.get("shop_name") or "",
                    "sales": item.get("sales") or "",
                    "key_specs": "; ".join(
                        f"{key}={value}"
                        for key, value in (item.get("attributes") or {}).items()
                        if value not in (None, "")
                    ),
                    "rating": reviews.get("rating") if reviews.get("rating") is not None else "",
                    "rating_count": reviews.get("rating_count") if reviews.get("rating_count") is not None else "",
                    "review_sample_size": reviews.get("sample_size") if reviews.get("sample_size") else "",
                    "negative_rate": reviews.get("negative_rate") if reviews.get("negative_rate") is not None else "",
                    "missing_fields": "|".join(item.get("missing_fields", []) or []),
                    "url": item.get("url") or "",
                    "elimination_reasons": "|".join(item.get("elimination_reasons", []) or []),
                }
            )


def render_outputs(
    report: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "report.json"
    markdown_path = directory / "report.md"
    csv_path = directory / "candidates.csv"
    save_json(json_path, report)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    write_csv(report, csv_path)
    return {"report_json": str(json_path), "report_md": str(markdown_path), "candidates_csv": str(csv_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render procurement memo artifacts")
    parser.add_argument("--brief", required=True)
    parser.add_argument("--candidates", required=True, help="scored candidates JSON")
    parser.add_argument("--scorecard", required=True)
    parser.add_argument("--bundle", default=None)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)

    brief = load_json(args.brief)
    payload = load_json(args.candidates)
    candidates = payload.get("candidates", payload) if isinstance(payload, dict) else payload
    scorecard = load_json(args.scorecard)
    bundle = load_json(args.bundle) if args.bundle else {}
    report = build_report(brief, candidates, scorecard, bundle)
    paths = render_outputs(report, args.output_dir)
    print(f"wrote {paths['report_md']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
