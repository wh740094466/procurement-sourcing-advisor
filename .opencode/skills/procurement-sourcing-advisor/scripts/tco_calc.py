"""Compute a guarded total cost of ownership (TCO).

TCO is only exact when the purchase price and every recurring/residual cost is
known.  Otherwise the result is a floor (and sometimes a ceiling), with the
missing components named explicitly.  This prevents a cheap listing price from
being presented as a complete ownership cost.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

try:
    from .common import load_json, round_money, save_json
except ImportError:  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import load_json, round_money, save_json  # type: ignore


COMPONENT_KEYS = (
    "consumables_per_year_cny",
    "energy_per_year_cny",
    "maintenance_per_year_cny",
    "accessories_cny",
    "resale_value_cny",
    "downtime_cost_per_day_cny",
    "downtime_days_per_year",
)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _key(candidate: dict[str, Any]) -> str:
    return f"{candidate.get('platform', '')}:{candidate.get('product_id', '')}"


def _model_for(candidate: dict[str, Any], model_doc: dict[str, Any] | None) -> tuple[dict[str, Any], float]:
    model_doc = model_doc or {}
    years = _number(model_doc.get("years", 3)) or 3.0
    defaults = model_doc.get("defaults", {}) if isinstance(model_doc.get("defaults"), dict) else {}
    by_product = model_doc.get("by_product", {}) if isinstance(model_doc.get("by_product"), dict) else {}
    product = by_product.get(_key(candidate)) or by_product.get(str(candidate.get("product_id"))) or {}
    inline = candidate.get("tco_inputs") if isinstance(candidate.get("tco_inputs"), dict) else {}
    merged: dict[str, Any] = {}
    for source in (defaults, product, inline):
        if isinstance(source, dict):
            merged.update({k: v for k, v in source.items() if k in COMPONENT_KEYS})
    if model_doc.get("years") is not None:
        years = _number(model_doc.get("years")) or years
    return merged, max(0.0, years)


def compute_tco(candidate: dict[str, Any], model_doc: dict[str, Any] | None = None) -> dict[str, Any]:
    inputs, years = _model_for(candidate, model_doc)
    landed = candidate.get("landed_price") or {}
    purchase_exact = _number(landed.get("landed_price"))
    purchase_floor = _number(landed.get("floor"))

    if purchase_exact is not None:
        purchase = purchase_exact
        purchase_status = "exact"
    elif purchase_floor is not None:
        purchase = purchase_floor
        purchase_status = "floor"
    else:
        purchase = None
        purchase_status = "unknown"

    def yearly(key: str) -> tuple[float | None, bool]:
        value = _number(inputs.get(key))
        return (value, True) if value is not None else (None, False)

    consumables, consumables_known = yearly("consumables_per_year_cny")
    energy, energy_known = yearly("energy_per_year_cny")
    maintenance, maintenance_known = yearly("maintenance_per_year_cny")
    accessories = _number(inputs.get("accessories_cny"))
    accessories_known = accessories is not None
    resale = _number(inputs.get("resale_value_cny"))
    resale_known = resale is not None
    downtime_day = _number(inputs.get("downtime_cost_per_day_cny"))
    downtime_days = _number(inputs.get("downtime_days_per_year"))
    downtime_known = downtime_day is not None and downtime_days is not None

    # Recurring costs are required for an exact TCO.  Accessories, resale, and
    # downtime are situational: if the buyer did not model them, they are
    # excluded *and named*, rather than silently treated as zero-sum facts.
    required: list[str] = []
    if purchase_status != "exact":
        required.append("purchase_exact")
    for label, known in (
        ("consumables", consumables_known),
        ("energy", energy_known),
        ("maintenance", maintenance_known),
    ):
        if not known:
            required.append(label)

    optional_excluded: list[str] = []
    for label, known in (
        ("accessories", accessories_known),
        ("resale", resale_known),
        ("downtime", downtime_known),
    ):
        if not known:
            optional_excluded.append(label)

    consumables_total = consumables * years if consumables_known else None
    energy_total = energy * years if energy_known else None
    maintenance_total = maintenance * years if maintenance_known else None
    downtime_total = downtime_day * downtime_days * years if downtime_known else None

    known_positive = sum(
        value
        for value in (
            purchase,
            consumables_total,
            energy_total,
            maintenance_total,
            accessories,
            downtime_total,
        )
        if value is not None
    )
    known_resale = max(0.0, resale) if resale_known else 0.0
    known_total = round_money(known_positive - known_resale) if purchase is not None else None

    exact_total = None
    if not required:
        exact_total = max(0.0, round_money(known_positive - known_resale) or 0.0)

    # If every required component is known, the only remaining uncertainty is
    # an unmodelled optional benefit (resale).  Treating resale as 0 gives a
    # conservative upper bound; nothing else can be assumed away.
    ceiling = None
    if purchase is not None and not required:
        ceiling = round_money(known_positive - known_resale)

    if exact_total is not None:
        status = "exact"
        note = "购买价与年度经常性成本均已显式提供"
        if optional_excluded:
            note += "；未建模的可选项按 0 计：" + "、".join(optional_excluded)
    elif known_total is not None:
        status = "partial"
        note = "TCO 为已知下限；缺少：" + "、".join(required)
    else:
        status = "unknown"
        note = "购买价不可用，无法计算 TCO"

    components = {
        "purchase": round_money(purchase),
        "purchase_status": purchase_status,
        "consumables": round_money(consumables_total),
        "energy": round_money(energy_total),
        "maintenance": round_money(maintenance_total),
        "accessories": round_money(accessories),
        "downtime": round_money(downtime_total),
        "resale": round_money(resale),
    }

    return {
        "years": years,
        "exact_total": exact_total,
        "floor": known_total,
        "ceiling": ceiling,
        "range": {"min": known_total, "max": ceiling},
        "status": status,
        "components": components,
        "missing_components": required,
        "optional_excluded": optional_excluded,
        "note": note,
        "inputs": inputs,
    }


def apply_tco(candidates: list[dict[str, Any]], model_doc: dict[str, Any] | None = None) -> dict[str, Any]:
    warnings: list[str] = []
    output: list[dict[str, Any]] = []
    for candidate in candidates:
        enriched = dict(candidate)
        enriched["tco"] = compute_tco(candidate, model_doc)
        tco = enriched["tco"]
        if tco["status"] == "partial":
            warnings.append(
                f"{candidate.get('platform')}:{candidate.get('product_id')} TCO 为下限/区间，缺少：{', '.join(tco['missing_components'])}"
            )
        elif tco["status"] == "unknown":
            warnings.append(f"{candidate.get('platform')}:{candidate.get('product_id')} 无法计算 TCO")
        output.append(enriched)
    return {"candidates": output, "warnings": warnings, "errors": []}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute guarded TCO")
    parser.add_argument("--input", required=True, help="priced candidates JSON")
    parser.add_argument("--model", default=None, help="TCO model JSON")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    payload = load_json(args.input)
    candidates = payload.get("candidates", payload) if isinstance(payload, dict) else payload
    model_doc = load_json(args.model) if args.model else {}
    result = apply_tco(candidates, model_doc)
    save_json(args.output, result)
    print(f"computed TCO for {len(result['candidates'])} candidates, warnings={len(result['warnings'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
