"""Validate a raw browser capture before any calculation happens.

The rule this module enforces is simple: a number without a verbatim snippet is
not evidence, it is a guess.  Everything downstream (pricing, TCO, scoring)
assumes the capture already passed this gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

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

        seen_variants: set[str] = set()
        for variant in capture.get("variants") or []:
            name = normalize_text(variant.get("label")) or "<unnamed>"
            vlabel = f"{label}/{name}"
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
