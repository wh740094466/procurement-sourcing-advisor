"""Turn raw browser captures into variant-level procurement candidates.

A market listing is a container, not a product.  The same link can sell a
27-inch monitor with a fixed stand for ¥629 and a 32-inch one with a gas lift
for ¥1,299.  Treating that link as one candidate silently compares different
products, so this module expands every variant (SKU/configuration) into its own
candidate and keeps the listing only as context.

Input contract: ``schemas/raw_capture.schema.json``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

try:
    from .common import (
        UNKNOWN,
        confidence_for_candidate,
        infer_model,
        infer_screen_from_title,
        is_missing,
        load_json,
        normalize_text,
        now_iso,
        parse_price,
        parse_specs,
        save_json,
        slugify_key,
    )
except ImportError:  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import (  # type: ignore
        UNKNOWN,
        confidence_for_candidate,
        infer_model,
        infer_screen_from_title,
        is_missing,
        load_json,
        normalize_text,
        now_iso,
        parse_price,
        parse_specs,
        save_json,
        slugify_key,
    )


def _specs_text(specs: Any) -> str:
    if not isinstance(specs, dict):
        return ""
    parts = []
    for key, value in specs.items():
        if is_missing(value):
            continue
        parts.append(f"{normalize_text(key)}: {normalize_text(value)}")
    return "; ".join(parts)


def _normalize_reviews(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "status": "missing",
            "rating": None,
            "positive_rate": None,
            "rating_count": None,
            "sample_size": 0,
            "negative_rate": None,
            "negative_themes": [],
            "positive_themes": [],
            "evidence": [],
        }

    def themes(items: Any) -> list[dict[str, Any]]:
        cleaned = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            theme = normalize_text(item.get("theme"))
            if not theme:
                continue
            try:
                count = max(1, int(item.get("count") or 1))
            except (TypeError, ValueError):
                count = 1
            cleaned.append(
                {
                    "theme": theme,
                    "count": count,
                    "snippets": [normalize_text(text) for text in (item.get("snippets") or []) if normalize_text(text)],
                }
            )
        return sorted(cleaned, key=lambda item: item["count"], reverse=True)

    sample_size = 0
    try:
        sample_size = max(0, int(raw.get("sample_size") or 0))
    except (TypeError, ValueError):
        sample_size = 0
    negatives = themes(raw.get("negative_themes"))
    negative_mentions = sum(item["count"] for item in negatives)
    negative_rate = round(min(1.0, negative_mentions / sample_size), 4) if sample_size else None

    rating = raw.get("rating")
    try:
        rating = round(float(rating), 2) if rating is not None else None
    except (TypeError, ValueError):
        rating = None
    positive_rate = raw.get("positive_rate")
    try:
        positive_rate = round(float(positive_rate), 2) if positive_rate is not None else None
    except (TypeError, ValueError):
        positive_rate = None
    rating_count = raw.get("rating_count")
    try:
        rating_count = int(rating_count) if rating_count is not None else None
    except (TypeError, ValueError):
        rating_count = None

    has_aggregate = rating is not None or positive_rate is not None
    has_evidence = bool(raw.get("evidence")) and has_aggregate
    return {
        "status": "ok" if has_evidence else "missing",
        "rating": rating,
        "positive_rate": positive_rate,
        "rating_count": rating_count,
        "sample_size": sample_size,
        "negative_rate": negative_rate,
        "negative_themes": negatives,
        "positive_themes": themes(raw.get("positive_themes")),
        "evidence": raw.get("evidence") or [],
    }


def _evidence(variant: dict[str, Any], capture: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for raw in variant.get("evidence") or []:
        if not isinstance(raw, dict):
            continue
        field = normalize_text(raw.get("field"))
        snippet = normalize_text(raw.get("snippet"))
        source_url = normalize_text(raw.get("source_url")) or normalize_text(capture.get("url"))
        if not field or not snippet or not source_url:
            continue
        items.append(
            {
                "field": field,
                "value": raw.get("value"),
                "snippet": snippet,
                "source_url": source_url,
                "captured_at": normalize_text(raw.get("captured_at")) or normalize_text(capture.get("captured_at")),
                "confidence": raw.get("confidence", 0.8),
                "source": "playwright",
            }
        )
    return items


def _price_for(variant: dict[str, Any]) -> tuple[float | None, str]:
    declared = normalize_text(variant.get("price_status"))
    if declared in {"hidden", "unknown"}:
        return None, declared
    if "price" in variant:
        value, status = parse_price(variant.get("price"), key_present=True)
        if value is not None:
            return value, status
    if not is_missing(variant.get("price_text")):
        return parse_price(variant.get("price_text"), key_present=True)
    return None, "unknown"


_OFFICIAL_FORBIDDEN_KEYS = {
    "price",
    "价格",
    "售价",
    "促销价",
    "stock",
    "库存",
    "stock_status",
}


def _select_official(capture: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any] | None:
    """Variant-level official page wins over the listing-level fallback."""

    for source in (variant.get("official"), capture.get("official")):
        if isinstance(source, dict) and source:
            return source
    return None


def _official_attributes(official: dict[str, Any]) -> dict[str, Any]:
    raw_specs = official.get("specs")
    if not isinstance(raw_specs, dict):
        return {}
    cleaned = {
        key: value
        for key, value in raw_specs.items()
        if normalize_text(key).lower() not in _OFFICIAL_FORBIDDEN_KEYS and not is_missing(value)
    }
    # Reuse the free-form parser so official labels go through _SPEC_KEY_MAP
    # (e.g. 重量 -> weight/weight_kg) just like listing spec tables.
    return parse_specs(_specs_text(cleaned)) if cleaned else {}


def _official_evidence(official: dict[str, Any], capture: dict[str, Any]) -> list[dict[str, Any]]:
    url = normalize_text(official.get("url")) or normalize_text(capture.get("url"))
    captured_at = normalize_text(official.get("captured_at")) or normalize_text(capture.get("captured_at"))
    items: list[dict[str, Any]] = []
    for raw in official.get("evidence") or []:
        if not isinstance(raw, dict):
            continue
        field = normalize_text(raw.get("field")) or "official.specs"
        if field.lower() in {"price", "stock", "stock_status"} or field.lower().startswith("price."):
            continue
        snippet = normalize_text(raw.get("snippet"))
        source_url = normalize_text(raw.get("source_url")) or url
        if not snippet or not source_url:
            continue
        items.append(
            {
                "field": field,
                "value": raw.get("value"),
                "snippet": snippet,
                "source_url": source_url,
                "captured_at": normalize_text(raw.get("captured_at")) or captured_at,
                "confidence": raw.get("confidence", 0.85),
                "source": "official",
            }
        )
    return items


def _warranty_from_text(text: str) -> str:
    """Pull an explicit warranty term out of an after-sales paragraph."""

    if not text:
        return ""
    match = re.search(
        r"(?:(\d+)\s*年|([一二三四五两])\s*年)\s*(?:质保|保修|保固)",
        text,
    )
    if not match:
        return ""
    if match.group(1):
        return f"{match.group(1)}年"
    digits = {"一": "1", "二": "2", "两": "2", "三": "3", "四": "4", "五": "5"}
    return f"{digits.get(match.group(2), match.group(2))}年"


def _candidate(
    capture: dict[str, Any],
    variant: dict[str, Any],
    reviews: dict[str, Any],
    fetched_at: str,
    warnings: list[str],
) -> dict[str, Any]:
    platform = capture["platform"]
    listing_id = normalize_text(capture["listing_id"])
    label = normalize_text(variant.get("label")) or UNKNOWN
    variant_id = normalize_text(variant.get("variant_id")) or f"{listing_id}-{slugify_key(label)}"
    listing_title = normalize_text(capture.get("title")) or UNKNOWN
    title = listing_title

    specs = _specs_text(variant.get("specs"))
    sections = capture.get("sections") if isinstance(capture.get("sections"), dict) else {}
    params_text = _specs_text(sections.get("params"))
    specs = "; ".join(part for part in (params_text, specs) if part)
    attributes: dict[str, Any] = parse_specs(specs) if specs else {}
    if "screen_inch" not in attributes:
        inferred = infer_screen_from_title(title)
        if inferred is not None:
            attributes["screen_inch"] = inferred
            attributes.setdefault("screen", f"{inferred:g}英寸")

    condition_text = " ".join(
        [title, label] + [normalize_text(item) for item in (variant.get("option_path") or [])]
    )
    if "官换" in condition_text:
        attributes.setdefault("condition", "官换")
    elif "全新" in condition_text or "原封" in condition_text:
        attributes.setdefault("condition", "全新")

    title_text = normalize_text(title).upper()
    if "recording" not in attributes and ("CMR" in title_text or "垂直" in title_text):
        attributes["recording"] = "CMR" if "CMR" in title_text else "垂直式"

    platform_warranty = normalize_text(attributes.get("warranty")) or normalize_text(variant.get("warranty"))
    official_source = _select_official(capture, variant)
    official_detail: dict[str, Any] | None = None
    official_evidence: list[dict[str, Any]] = []
    if isinstance(official_source, dict):
        official_status = normalize_text(official_source.get("match_status")) or "not_found"
        official_attributes = _official_attributes(official_source)
        official_evidence = _official_evidence(official_source, capture)
        filled: list[str] = []
        conflicts: list[dict[str, Any]] = []
        if official_status == "exact":
            for key, value in official_attributes.items():
                if key not in attributes or is_missing(attributes.get(key)):
                    attributes[key] = value
                    filled.append(key)
                elif normalize_text(attributes.get(key)) != normalize_text(value):
                    conflicts.append(
                        {
                            "field": key,
                            "platform": attributes.get(key),
                            "official": value,
                        }
                    )
        official_detail = {
            "manufacturer": normalize_text(official_source.get("manufacturer")),
            "model": normalize_text(official_source.get("model")),
            "region": normalize_text(official_source.get("region")),
            "url": normalize_text(official_source.get("url")),
            "captured_at": normalize_text(official_source.get("captured_at")),
            "match_status": official_status,
            "matched_text": normalize_text(official_source.get("matched_text")),
            "specs": official_attributes,
            "filled_attributes": filled,
            "conflicts": conflicts,
            "notes": [normalize_text(note) for note in (official_source.get("notes") or []) if normalize_text(note)],
            "evidence": official_evidence,
        }
        if official_status == "exact":
            if filled:
                warnings.append(
                    f"{platform}:{variant_id} 官网 exact 匹配补全 {len(filled)} 个规格字段"
                    f"（{official_detail['model']}）"
                )
        else:
            warnings.append(f"{platform}:{variant_id} 官网 match_status={official_status}，未采用官网参数")
        if conflicts:
            warnings.append(
                f"{platform}:{variant_id} 官网参数与商品页冲突 {len(conflicts)} 项，保留商品页值"
            )

    price, price_status = _price_for(variant)
    if price_status == "visible" and price is None:
        price_status = "unknown"
    if price is None:
        warnings.append(f"{platform}:{variant_id} 价格缺失/隐藏（{label}），不参与价格排序")

    detail = {
        "specs": specs or UNKNOWN,
        "warranty": normalize_text(attributes.get("warranty")) or UNKNOWN,
        "invoice": normalize_text(attributes.get("invoice")) or UNKNOWN,
        "stock_status": normalize_text(variant.get("stock_status")) or normalize_text(attributes.get("stock_status")) or UNKNOWN,
        "after_sales": normalize_text(sections.get("after_sales")),
        "usage_notes": normalize_text(sections.get("usage")),
        "warranty_source": "",
    }
    for field in ("warranty", "invoice", "stock_status"):
        explicit = normalize_text(variant.get(field))
        if explicit and detail[field] == UNKNOWN:
            detail[field] = explicit
    if detail["warranty"] != UNKNOWN:
        detail["warranty_source"] = "platform"
        if (
            official_detail
            and official_detail.get("match_status") == "exact"
            and "warranty" in (official_detail.get("filled_attributes") or [])
            and not platform_warranty
        ):
            detail["warranty_source"] = "official"
            warnings.append(
                f"{platform}:{variant_id} 保修年限来自官网 {official_detail.get('model')}，"
                "下单前需确认销售渠道的承保主体"
            )

    list_price, _ = parse_price(variant.get("list_price"), key_present="list_price" in variant)

    candidate = {
        "platform": platform,
        "listing_id": listing_id,
        "listing_title": listing_title,
        "product_id": variant_id,
        "variant_id": variant_id,
        "variant_label": label,
        "option_path": [normalize_text(item) for item in (variant.get("option_path") or []) if normalize_text(item)],
        "title": title,
        "model": infer_model(title, specs, variant.get("model")),
        "price": price,
        "price_status": price_status,
        "currency": "CNY",
        "orig_price": list_price,
        "shop_name": normalize_text(capture.get("shop"))
        or normalize_text((sections.get("shop") or {}).get("name"))
        or UNKNOWN,
        "sales": capture.get("sales"),
        "url": normalize_text(capture.get("url")),
        "detail": detail,
        "attributes": attributes,
        "reviews": reviews,
        "sections": {
            "qa": sections.get("qa") or [],
            "notes": sections.get("notes") or [],
            "recommendations": sections.get("recommendations") or [],
            "shop": sections.get("shop") or {},
        },
        "filter_text": " ".join(
            [title, label] + [normalize_text(item) for item in (variant.get("option_path") or [])]
        ),
        "excluded_early": bool(capture.get("aborted")),
        "exclusion_reason": normalize_text(capture.get("abort_reason")),
        "fetched_at": fetched_at,
        "detail_fetched_at": fetched_at,
        "confidence": 0.0,
        "price_note": "",
        "flags": {"ad": bool(capture.get("ad", False))},
        "evidence": _evidence(variant, capture) + official_evidence,
        "browser_evidence": [],
        "price_observations": [],
        "sources": [{"type": "playwright", "captured_at": fetched_at, "url": normalize_text(capture.get("url"))}]
        + (
            [
                {
                    "type": "official",
                    "captured_at": official_detail.get("captured_at") or fetched_at,
                    "url": official_detail.get("url"),
                    "model": official_detail.get("model"),
                    "match_status": official_detail.get("match_status"),
                }
            ]
            if official_detail
            else []
        ),
        "manual_scores": {},
        "needs_review": price is None
        or not candidate_has_price_evidence(variant)
        or bool((official_detail or {}).get("conflicts")),
    }
    if official_detail:
        candidate["official"] = official_detail

    if any(
        normalize_text(item.get("field")) == "price" and normalize_text(item.get("snippet"))
        for item in variant.get("evidence") or []
        if isinstance(item, dict)
    ):
        candidate["price_note"] = "价格为当前选中配置的页面报价（" + label + "）"
    price_source = next(
        (
            item["source_url"]
            for item in candidate["evidence"]
            if item.get("field") == "price" and item.get("source_url")
        ),
        None,
    )
    if price_source and price_source != candidate["url"]:
        candidate["listing_url"] = candidate["url"]
        candidate["url"] = price_source
    candidate["confidence"] = confidence_for_candidate(candidate)
    return candidate


def candidate_has_price_evidence(variant: dict[str, Any]) -> bool:
    for item in variant.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        if normalize_text(item.get("field")) == "price" and normalize_text(item.get("snippet")):
            return True
    return False


def normalize_capture(payload: dict[str, Any], *, fetched_at: str | None = None) -> dict[str, Any]:
    fetched_at = fetched_at or now_iso()
    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[dict[str, Any]] = []
    listings: dict[tuple[str, str], dict[str, Any]] = {}
    seen: set[tuple[str, str]] = set()

    for capture in payload.get("captures") or []:
        if not isinstance(capture, dict):
            errors.append({"stage": "listing", "message": "capture 不是对象"})
            continue
        platform = normalize_text(capture.get("platform"))
        listing_id = normalize_text(capture.get("listing_id"))
        if platform not in {"taobao", "jd"} or not listing_id:
            errors.append({"stage": "listing", "message": f"capture 缺少 platform/listing_id: {capture.get('title')!r}"})
            continue
        if capture.get("page_state") != "ok":
            errors.append(
                {
                    "platform": platform,
                    "listing_id": listing_id,
                    "stage": "listing",
                    "message": f"page_state={capture.get('page_state')}，未采集",
                }
            )
            continue

        reviews = _normalize_reviews(capture.get("reviews"))
        listings[(platform, listing_id)] = {
            "title": normalize_text(capture.get("title")),
            "url": normalize_text(capture.get("url")),
            "variants": len(capture.get("variants") or []),
            "reviews_status": reviews["status"],
            "review_sample_size": reviews["sample_size"],
        }

        for variant in capture.get("variants") or []:
            if not isinstance(variant, dict):
                continue
            candidate = _candidate(capture, variant, reviews, fetched_at, warnings)
            key = (candidate["platform"], candidate["product_id"])
            if key in seen:
                warnings.append(f"{key[0]}:{key[1]} 变体重复出现，已保留第一次采集结果")
                continue
            seen.add(key)
            candidates.append(candidate)

    errors.extend(payload.get("errors") or [])
    summary = {
        "listings": len(listings),
        "total": len(candidates),
        "by_platform": {
            platform: sum(1 for item in candidates if item["platform"] == platform)
            for platform in ("taobao", "jd")
        },
        "with_reviews": sum(1 for item in candidates if item["reviews"]["status"] == "ok"),
        "with_official_specs": sum(
            1
            for item in candidates
            if (item.get("official") or {}).get("match_status") == "exact"
        ),
        "hidden_price": sum(1 for item in candidates if item["price_status"] != "visible"),
    }
    return {
        "generated_at": fetched_at,
        "summary": summary,
        "listings": [
            {"platform": platform, "listing_id": listing_id, **meta}
            for (platform, listing_id), meta in sorted(listings.items())
        ],
        "candidates": candidates,
        "warnings": warnings,
        "errors": errors,
    }


def normalize_payload(payload: Any, *, fetched_at: str | None = None) -> dict[str, Any]:
    """Compatibility shim: accept either {"captures": [...]} or a bare list."""

    if isinstance(payload, list):
        payload = {"captures": payload}
    if not isinstance(payload, dict):
        payload = {"captures": []}
    return normalize_capture(payload, fetched_at=fetched_at)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize raw_capture.json into variant candidates")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    payload = load_json(args.input)
    result = normalize_capture(payload)
    save_json(args.output, result)
    print(f"normalized: listings={result['summary']['listings']} candidates={result['summary']['total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
