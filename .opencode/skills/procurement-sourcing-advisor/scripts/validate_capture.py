"""Validate a raw browser capture before any calculation happens.

The rule this module enforces is simple: a number without a verbatim snippet is
not evidence, it is a guess.  Everything downstream (pricing, TCO, scoring)
assumes the capture already passed this gate.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from .common import is_missing, load_json, normalize_text
except ImportError:  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import is_missing, load_json, normalize_text  # type: ignore

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "raw_capture.schema.json"


def _schema_errors(payload: dict[str, Any]) -> list[str]:
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - jsonschema is a declared dependency
        return ["jsonschema 未安装，无法按 schema 校验"]
    schema = load_json(SCHEMA_PATH)
    validator = jsonschema.Draft202012Validator(schema)
    problems = []
    for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.path)):
        location = "/".join(str(part) for part in error.path) or "<root>"
        problems.append(f"{location}: {error.message}")
    return problems


def _has_snippet(evidence: list[Any], field: str) -> bool:
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        if normalize_text(item.get("field")) != field:
            continue
        if normalize_text(item.get("snippet")) and normalize_text(item.get("source_url")):
            return True
    return False


_OFFICIAL_FORBIDDEN_KEYS = {
    "price",
    "价格",
    "售价",
    "促销价",
    "stock",
    "库存",
    "stock_status",
}
_ECOMMERCE_DOMAINS = ("jd.com", "jd.hk", "taobao.com", "tmall.com")


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalize_text(value).lower())


def _check_official(label: str, official: Any, errors: list[str], warnings: list[str]) -> None:
    """Validate the optional manufacturer-page block.

    The official page is a spec source, never a price source.  Exact matching is
    the only status that may feed scoring; family/not_found records are kept for
    audit but must not pretend to be the same product.
    """

    if not isinstance(official, dict):
        return
    status = normalize_text(official.get("match_status"))
    url = normalize_text(official.get("url"))
    if url:
        host = urlparse(url).netloc.lower()
        if any(host == domain or host.endswith("." + domain) for domain in _ECOMMERCE_DOMAINS):
            errors.append(f"{label}: 官网来源 URL 不能是电商平台域名（{url}）")

    specs = official.get("specs")
    if isinstance(specs, dict):
        forbidden = [normalize_text(key) for key in specs if normalize_text(key).lower() in _OFFICIAL_FORBIDDEN_KEYS]
        if forbidden:
            errors.append(f"{label}: 官网参数块不得包含价格/库存字段：{', '.join(forbidden)}")

    evidence = [item for item in (official.get("evidence") or []) if isinstance(item, dict)]
    for item in evidence:
        field = normalize_text(item.get("field")).lower()
        if field in {"price", "stock", "stock_status"} or field.startswith("price."):
            errors.append(f"{label}: 官网证据只能用于规格，不能用于价格/库存字段（{field}）")

    if status == "exact":
        if not normalize_text(official.get("matched_text")):
            errors.append(f"{label}: 官网 exact 匹配缺少 matched_text")
        if not isinstance(specs, dict) or not specs:
            errors.append(f"{label}: 官网 exact 匹配缺少 specs")
        if not evidence:
            errors.append(f"{label}: 官网 exact 匹配缺少 evidence")
        elif not any(
            normalize_text(item.get("snippet")) and normalize_text(item.get("source_url"))
            for item in evidence
        ):
            errors.append(f"{label}: 官网 exact 匹配缺少带 snippet/source_url 的证据")
        model = _compact(official.get("model"))
        matched = _compact(official.get("matched_text"))
        if model and matched and model not in matched:
            warnings.append(f"{label}: 官网 matched_text 未直接包含型号 {official.get('model')}，请复核是否同型号")
    elif status in {"family", "not_found"} and specs:
        warnings.append(f"{label}: 官网 match_status={status}，specs 不会进入评分")


def validate_capture(payload: dict[str, Any]) -> dict[str, Any]:
    errors = list(_schema_errors(payload)) if isinstance(payload, dict) else ["capture 必须是 JSON 对象"]
    warnings: list[str] = []
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings, "listings": 0, "variants": 0}

    seen_listings: set[tuple[str, str]] = set()
    total_variants = 0
    for index, capture in enumerate(payload.get("captures") or []):
        platform = capture.get("platform")
        listing_id = normalize_text(capture.get("listing_id"))
        label = f"{platform}:{listing_id or index}"
        total_variants += len(capture.get("variants") or [])

        if (platform, listing_id) in seen_listings:
            errors.append(f"{label}: listing 重复出现，请合并为一条 capture")
        seen_listings.add((platform, listing_id))

        if capture.get("page_state") != "ok":
            errors.append(f"{label}: page_state={capture.get('page_state')}，不能作为数据来源")
            continue

        _check_official(label, capture.get("official"), errors, warnings)

        seen_variants: set[str] = set()
        for variant in capture.get("variants") or []:
            name = normalize_text(variant.get("label")) or "<unnamed>"
            vlabel = f"{label}/{name}"
            _check_official(vlabel, variant.get("official"), errors, warnings)
            variant_id = normalize_text(variant.get("variant_id")) or name
            if variant_id in seen_variants:
                errors.append(f"{vlabel}: 同一 listing 内 variant_id 重复（{variant_id}）")
            seen_variants.add(variant_id)

            price_status = normalize_text(variant.get("price_status")) or "visible"
            if price_status == "visible" and not _has_snippet(variant.get("evidence") or [], "price"):
                errors.append(f"{vlabel}: 价格可见但没有 price 字段的 snippet 证据")
            if is_missing(variant.get("price")) and is_missing(variant.get("price_text")):
                if price_status not in {"hidden", "unknown"}:
                    warnings.append(f"{vlabel}: 未提供 price/price_text，将按 hidden 处理")

        reviews = capture.get("reviews")
        if isinstance(reviews, dict):
            sample_size = reviews.get("sample_size") or 0
            if sample_size and not reviews.get("evidence"):
                errors.append(f"{label}: 有评价样本但没有 evidence")
            negatives = reviews.get("negative_themes") or []
            if sample_size and sum(int(item.get("count") or 0) for item in negatives) > sample_size:
                warnings.append(f"{label}: 负评主题计数超过 sample_size，可能是多次计数")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "listings": len(seen_listings),
        "variants": total_variants,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate raw_capture.json")
    parser.add_argument("path", help="path to raw_capture.json")
    args = parser.parse_args(argv)
    result = validate_capture(load_json(args.path))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
