"""Run the full offline procurement pipeline from a workspace directory.

Expected workspace files::

    brief.json
    raw_capture.json
    scorecard.json
    costs.json                (optional)
    tco_model.json            (optional)
    bundle_options.json       (optional)

`raw_capture.json` is produced by the AI + playwright-mcp collection stage and
contains listing -> variant -> review evidence.  The script never opens a
browser: it is the reproducible core that runs after collection.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

try:
    from .common import load_json, now_iso, save_json
    from .bundle_optimizer import optimize_bundle
    from .landed_price import apply_landed_prices
    from .normalize_candidates import normalize_capture
    from .price_anchor import apply_price_anchors
    from .rank_candidates import assign_ranks
    from .render_report import build_report, render_outputs
    from .requirement_fit import evaluate_all
    from .score_candidates import score_candidates
    from .tco_calc import apply_tco
    from .value_analysis import analyze_value
    from .validate_capture import validate_capture
except ImportError:  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import load_json, now_iso, save_json  # type: ignore
    from bundle_optimizer import optimize_bundle  # type: ignore
    from landed_price import apply_landed_prices  # type: ignore
    from normalize_candidates import normalize_capture  # type: ignore
    from price_anchor import apply_price_anchors  # type: ignore
    from rank_candidates import assign_ranks  # type: ignore
    from render_report import build_report, render_outputs  # type: ignore
    from requirement_fit import evaluate_all  # type: ignore
    from score_candidates import score_candidates  # type: ignore
    from tco_calc import apply_tco  # type: ignore
    from value_analysis import analyze_value  # type: ignore
    from validate_capture import validate_capture  # type: ignore


def _brief_defaults(brief: dict[str, Any]) -> dict[str, Any]:
    result = dict(brief)
    result["quantity"] = max(1, int(result.get("quantity") or 1))
    budget = result.get("budget_cny")
    if result.get("budget_per_unit_cny") is None and isinstance(budget, (int, float)):
        result["budget_per_unit_cny"] = round(float(budget) / result["quantity"], 2)
    result.setdefault("must_have", [])
    result.setdefault("nice_to_have", [])
    result.setdefault("exclusions", [])
    result.setdefault("allow_split", False)
    result.setdefault("allow_alternative_models", True)
    return result


def run_pipeline(
    workspace: str | Path,
    *,
    brief_path: str | Path | None = None,
    capture_path: str | Path | None = None,
    scorecard_path: str | Path | None = None,
    costs_path: str | Path | None = None,
    tco_model_path: str | Path | None = None,
    bundle_options_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    report_id: str | None = None,
    require_confirmation: bool = True,
) -> dict[str, Any]:
    workspace = Path(workspace)
    brief_path = Path(brief_path) if brief_path else workspace / "brief.json"
    capture_path = Path(capture_path) if capture_path else workspace / "raw_capture.json"
    scorecard_path = Path(scorecard_path) if scorecard_path else workspace / "scorecard.json"
    costs_path = Path(costs_path) if costs_path else workspace / "costs.json"
    tco_model_path = Path(tco_model_path) if tco_model_path else workspace / "tco_model.json"
    bundle_options_path = Path(bundle_options_path) if bundle_options_path else workspace / "bundle_options.json"
    output_dir = Path(output_dir) if output_dir else workspace / "output"

    for required in (brief_path, capture_path, scorecard_path):
        if not required.exists():
            raise FileNotFoundError(f"required input not found: {required}")

    brief = _brief_defaults(load_json(brief_path))
    capture = load_json(capture_path)
    validation = validate_capture(capture)
    if not validation["ok"]:
        preview = "; ".join(validation["errors"][:5])
        raise ValueError(f"raw_capture.json 未通过校验：{preview}")

    scorecard = load_json(scorecard_path)
    costs = load_json(costs_path) if costs_path.exists() else {}
    tco_model = load_json(tco_model_path) if tco_model_path.exists() else {"years": 3}
    bundle_options = load_json(bundle_options_path) if bundle_options_path.exists() else {}

    brief_confirmed = (brief.get("confirmed") or {}).get("by_user")
    scorecard_confirmed = (scorecard.get("confirmed") or {}).get("by_user")
    if require_confirmation and not (brief_confirmed and scorecard_confirmed):
        raise ValueError(
            "需求与评比维度尚未确认：先运行 render_confirmation.py 生成确认单，"
            "用户确认后用 freeze_plan.py 冻结；临时跳过可加 --allow-unconfirmed"
        )

    normalized = normalize_capture(capture)
    anchor_config = scorecard.get("price_anchor") if isinstance(scorecard.get("price_anchor"), dict) else {}
    anchors = apply_price_anchors(
        normalized["candidates"],
        min_ratio=float(anchor_config.get("min_ratio", 0.7)),
    )
    priced = apply_landed_prices(normalized["candidates"], costs)
    with_tco = apply_tco(priced["candidates"], tco_model)
    staged = bool(scorecard.get("stages"))
    staged_warning = None
    fit_summary: dict[str, Any] | None = None
    value_summary: dict[str, Any] | None = None
    rank_summary: dict[str, Any] | None = None
    if staged:
        scored = score_candidates(with_tco["candidates"], scorecard, brief, assign_rank=False)
        fit_threshold = float((scorecard.get("stages") or {}).get("fit_threshold", 0.8))
        fit_summary = evaluate_all(
            scored["candidates"],
            brief.get("requirements") or [],
            fit_threshold=fit_threshold,
        )
        value_summary = analyze_value(
            scored["candidates"],
            scorecard.get("dimensions") or [],
            fit_threshold=fit_threshold,
        )
        rank_summary = assign_ranks(scored["candidates"])
    else:
        scored = score_candidates(with_tco["candidates"], scorecard, brief)
        staged_warning = "评分卡未分组（fit/value/risk），本次使用旧版加权总分排序"
    bundle = optimize_bundle(scored["candidates"], brief, bundle_options)

    warnings = list(validation.get("warnings", []))
    warnings.extend(normalized.get("warnings", []))
    if staged_warning:
        warnings.append(staged_warning)
    if anchors.get("anomalies"):
        ratio = float(anchors.get("min_ratio", 0.7))
        warnings.append(
            f"{len(anchors['anomalies'])} 条候选价格低于官方/自营锚点的 {ratio:.0%}，"
            "已限制其价格得分并要求先证实成色/发票/保修主体"
        )
    warnings.extend(priced.get("warnings", []))
    warnings.extend(with_tco.get("warnings", []))
    warnings.extend(scored.get("warnings", []))
    warnings.extend(bundle.get("warnings", []))
    errors = list(normalized.get("errors", []))

    report = build_report(
        brief,
        scored["candidates"],
        scorecard,
        bundle,
        warnings=warnings,
        errors=errors,
        price_anchors=anchors,
        fit_summary=fit_summary,
        value_summary=value_summary,
        methodology={
            "stages": [
                "需求形式化（含配置敏感项）",
                "playwright-mcp 搜索召回 listing",
                "逐 listing 展开配置（每个 SKU 单独读价）",
                "商品页评价采集（评分/评价数/负评主题）",
                "具体型号官网参数核对（仅补规格，不取价）",
                "归一化与价格状态标记",
                "到手价/TCO",
                "硬过滤与加权评分",
                "拆单/凑单评估",
                "报告与自检",
            ],
            "inputs": {
                "raw_capture": str(capture_path),
                "scorecard": str(scorecard_path),
                "costs": str(costs_path) if costs_path.exists() else None,
                "tco_model": str(tco_model_path) if tco_model_path.exists() else None,
            },
            "missing_policy": "缺失字段保留 UNKNOWN；评分按评分卡缺失策略处理；置信度独立展示。",
            "scorecard_note": "评分项由 AI 根据实际物品生成，经 JSON 校验后由脚本确定性计算。",
        },
        report_id=report_id,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    save_json(output_dir / "normalized.json", normalized)
    save_json(output_dir / "priced.json", priced)
    save_json(output_dir / "tco.json", with_tco)
    save_json(output_dir / "scored.json", scored)
    save_json(output_dir / "bundle.json", bundle)
    paths = render_outputs(report, output_dir)

    manifest = {
        "generated_at": now_iso(),
        "workspace": str(workspace),
        "inputs": {
            "brief": str(brief_path),
            "raw_capture": str(capture_path),
            "scorecard": str(scorecard_path),
            "costs": str(costs_path) if costs_path.exists() else None,
            "tco_model": str(tco_model_path) if tco_model_path.exists() else None,
            "bundle_options": str(bundle_options_path) if bundle_options_path.exists() else None,
        },
        "capture_validation": validation,
        "price_anchors": anchors,
        "fit": fit_summary,
        "value": value_summary,
        "ranking": rank_summary,
        "confirmation": {
            "brief": brief.get("confirmed"),
            "scorecard": scorecard.get("confirmed"),
        },
        "summary": {
            "normalized": normalized.get("summary", {}),
            "scored": scored.get("summary", {}),
            "bundle_recommendation": bundle.get("recommended_option"),
            "report": report["summary"],
        },
        "outputs": {
            **paths,
            "normalized": str(output_dir / "normalized.json"),
            "priced": str(output_dir / "priced.json"),
            "tco": str(output_dir / "tco.json"),
            "scored": str(output_dir / "scored.json"),
            "bundle": str(output_dir / "bundle.json"),
        },
        "warnings": warnings,
        "errors": errors,
    }
    save_json(output_dir / "run_manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the offline procurement pipeline")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--brief", default=None)
    parser.add_argument("--capture", default=None)
    parser.add_argument("--scorecard", default=None)
    parser.add_argument("--costs", default=None)
    parser.add_argument("--tco-model", default=None)
    parser.add_argument("--bundle-options", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--report-id", default=None)
    parser.add_argument("--allow-unconfirmed", action="store_true", help="skip the requirement/dimension confirmation gate")
    args = parser.parse_args(argv)

    manifest = run_pipeline(
        args.workspace,
        brief_path=args.brief,
        capture_path=args.capture,
        scorecard_path=args.scorecard,
        costs_path=args.costs,
        tco_model_path=args.tco_model,
        bundle_options_path=args.bundle_options,
        output_dir=args.output_dir,
        report_id=args.report_id,
        require_confirmation=not args.allow_unconfirmed,
    )
    print(
        f"pipeline complete: listings={manifest['summary']['normalized'].get('listings')}, "
        f"variants={manifest['summary']['normalized'].get('total')}, "
        f"eligible={manifest['summary']['scored'].get('eligible')}, "
        f"report={manifest['outputs']['report_md']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
