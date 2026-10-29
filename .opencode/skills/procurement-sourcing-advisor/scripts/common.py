"""Shared helpers for the procurement-sourcing-advisor skill.

The helpers in this module deliberately keep "missing" separate from "zero".
That distinction is the core data-safety rule of the skill: a hidden or absent
price must never become 0, and an absent warranty must never become "no
warranty".
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

UNKNOWN = "UNKNOWN"

_EMPTY_STRINGS = {"", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "UNSET", "TBD", "-", "—"}
_PRICE_JUNK = re.compile(r"[¥￥,，\s]")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

_SPEC_KEY_MAP = {
    "cpu": "cpu",
    "处理器": "cpu",
    "processor": "cpu",
    "gpu": "gpu",
    "显卡": "gpu",
    "内存": "ram",
    "运行内存": "ram",
    "ram": "ram",
    "memory": "ram",
    "硬盘": "storage",
    "存储": "storage",
    "固态硬盘": "storage",
    "ssd": "storage",
    "storage": "storage",
    "屏幕": "screen",
    "屏幕尺寸": "screen",
    "screen": "screen",
    "分辨率": "resolution",
    "resolution": "resolution",
    "面板": "panel",
    "面板类型": "panel",
    "panel": "panel",
    "刷新率": "refresh_rate",
    "屏幕刷新率": "refresh_rate",
    "refresh rate": "refresh_rate",
    "response time": "response_time",
    "响应时间": "response_time",
    "色域": "color_gamut",
    "接口": "ports",
    "支架": "stand",
    "支架底座": "stand",
    "对比度": "contrast_ratio",
    "亮度": "brightness",
    "壁挂规格": "vesa",
    "是否内置音箱": "builtin_speaker",
    "屏幕比例": "aspect_ratio",
    "品牌": "brand",
    "认证型号": "certified_model",
    "电池": "battery",
    "续航": "battery",
    "battery": "battery",
    "重量": "weight",
    "weight": "weight",
    "型号": "model",
    "model": "model",
    "保修": "warranty",
    "质保": "warranty",
    "保修期": "warranty",
    "质保期": "warranty",
    "warranty": "warranty",
    "发票": "invoice",
    "invoice": "invoice",
    "库存": "stock_status",
    "stock": "stock_status",
    "现货": "stock_status",
    "颜色": "color",
    "尺寸": "size",
    "材质": "material",
    "面料": "fabric",
    "容量": "capacity",
    "缓存": "cache",
    "缓存容量": "cache",
    "转速": "rpm",
    "接口类型": "ports",
    "记录技术": "recording",
    "硬盘类型": "drive_type",
    "盘片数量": "platters",
    "工作负载": "workload",
    "平均无故障时间": "mtbf",
    "承重": "load_capacity",
    "认证": "certification",
    "执行标准": "standard",
    "交期": "lead_time",
    "功率": "power_w",
    "噪音": "noise_db",
    "能效": "energy_rating",
    "产地": "origin",
    "腰托": "lumbar_support",
    "头枕": "headrest",
    "扶手": "armrest",
    "椅背": "backrest",
    "气压棒": "gas_lift",
    "脚轮": "casters",
    "座深": "seat_depth",
    "座高": "seat_height",
}


def now_iso() -> str:
    """Return a timezone-aware ISO-8601 timestamp."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\u3000", " ")
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def is_unknown(value: Any) -> bool:
    """True when a value carries no usable evidence.

    Numeric zero is *not* unknown.  A price of zero is handled separately by
    :func:`parse_price`, because zero is almost always a display placeholder.
    """

    if value is None:
        return True
    if isinstance(value, str):
        return normalize_text(value).upper() in _EMPTY_STRINGS
    return False


def is_missing(value: Any) -> bool:
    if is_unknown(value):
        return True
    if isinstance(value, (list, tuple, dict, set)) and len(value) == 0:
        return True
    return False


def parse_price(value: Any, *, key_present: bool = True) -> tuple[float | None, str]:
    """Parse a listing price without inventing a value.

    Returns ``(price, status)`` where status is ``visible``, ``hidden`` or
    ``unknown``.  A present-but-empty value is ``hidden``; an absent field is
    ``unknown``.  Zero and negative values are treated as hidden.
    """

    if not key_present:
        return None, "unknown"
    if value is None:
        return None, "hidden"
    if isinstance(value, bool):
        return None, "hidden"
    if isinstance(value, (int, float)):
        number = float(value)
        return (round(number, 2), "visible") if number > 0 else (None, "hidden")

    text = normalize_text(value)
    if not text or text.upper() in _EMPTY_STRINGS:
        return None, "hidden"
    if any(token in text for token in ("面议", "询价", "登录后可见", "价格保密")):
        return None, "hidden"

    cleaned = _PRICE_JUNK.sub("", text)
    match = _NUMBER.search(cleaned)
    if not match:
        return None, "hidden"
    try:
        number = float(match.group(0))
    except ValueError:
        return None, "hidden"
    return (round(number, 2), "visible") if number > 0 else (None, "hidden")


def parse_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUMBER.search(normalize_text(value))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def parse_quantity(value: Any, default: int = 1) -> int:
    number = parse_number(value)
    if number is None:
        return default
    return max(1, int(number))


def round_money(value: float | None) -> float | None:
    return None if value is None else round(float(value) + 1e-9, 2)


def get_path(obj: Any, path: str | None, default: Any = None) -> Any:
    """Read a dotted path from nested dicts/lists.

    ``candidate["attributes"]["cpu"]`` is addressed as
    ``attributes.cpu``.  List indices are supported as numeric segments.
    """

    if not path:
        return default
    current = obj
    for part in str(path).split("."):
        if isinstance(current, dict):
            if part not in current:
                return default
            current = current[part]
        elif isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return default
            current = current[index]
        else:
            return default
    return current


def set_path(obj: dict[str, Any], path: str, value: Any) -> None:
    parts = str(path).split(".")
    current: dict[str, Any] = obj
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def slugify_key(value: Any) -> str:
    text = normalize_text(value).lower()
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "_", text)
    return text.strip("_") or "attribute"


def _derive_numeric_attributes(attributes: dict[str, Any]) -> None:
    """Add common numeric derivatives only when the unit is explicit."""

    def add(key: str, source: str, pattern: str, factor: float = 1.0) -> None:
        if key in attributes:
            return
        raw = normalize_text(attributes.get(source))
        match = re.search(pattern, raw, re.IGNORECASE)
        if not match:
            return
        try:
            attributes[key] = round(float(match.group(1)) * factor, 2)
        except (TypeError, ValueError):
            return

    add("ram_gb", "ram", r"(\d+(?:\.\d+)?)\s*(?:gb|g|G)\b", 1.0)
    add("storage_gb", "storage", r"(\d+(?:\.\d+)?)\s*(?:tb|t)\b", 1000.0)
    add("storage_gb", "storage", r"(\d+(?:\.\d+)?)\s*(?:gb|g)\b", 1.0)
    add("weight_kg", "weight", r"(\d+(?:\.\d+)?)\s*(?:kg|千克|公斤)\b", 1.0)
    add("weight_kg", "weight", r"(\d+(?:\.\d+)?)\s*(?:g|克)\b", 0.001)
    add("screen_inch", "screen", r"(\d+(?:\.\d+)?)\s*(?:英寸|吋|inch|\")", 1.0)


def parse_specs(value: Any) -> dict[str, Any]:
    """Turn a free-form spec string into a conservative attribute map.

    Only explicit ``key: value`` pairs are trusted.  We do not infer CPU tier,
    warranty length, or stock from prose, because those inferences are exactly
    where fabricated procurement facts creep in.
    """

    if isinstance(value, dict):
        attributes = {slugify_key(k): v for k, v in value.items() if not is_missing(v)}
    elif isinstance(value, str) and not is_unknown(value):
        attributes: dict[str, Any] = {}
        parts = re.split(r"[;；\n|]+", value)
        for part in parts:
            part = normalize_text(part)
            if not part:
                continue
            match = re.match(r"^([^:：]{1,40})[:：]\s*(.+)$", part)
            if not match:
                continue
            label, raw_value = normalize_text(match.group(1)), normalize_text(match.group(2))
            if is_missing(raw_value):
                continue
            key = _SPEC_KEY_MAP.get(label.lower(), _SPEC_KEY_MAP.get(label, slugify_key(label)))
            attributes[key] = raw_value
    else:
        attributes = {}

    _derive_numeric_attributes(attributes)
    return attributes


def infer_model(title: Any, specs: Any, explicit: Any = None) -> str:
    """Return an explicitly stated model, never a guessed marketing name."""

    if not is_unknown(explicit):
        return normalize_text(explicit)
    if isinstance(specs, dict) and not is_unknown(specs.get("model")):
        return normalize_text(specs.get("model"))
    text = normalize_text(specs if isinstance(specs, str) else "")
    match = re.search(r"(?:型号|model)\s*[:：]\s*([^;；,，|\n]+)", text, re.IGNORECASE)
    if match:
        return normalize_text(match.group(1))
    return UNKNOWN


def infer_screen_from_title(title: Any) -> float | None:
    """Read an explicit ``27英寸`` / ``27寸`` from the product name.

    Titles state the diagonal explicitly, so this is extraction rather than
    inference.  It is bounded to plausible monitor/panel sizes to avoid
    picking up unrelated numbers (e.g. "2026款").
    """

    text = normalize_text(title if isinstance(title, str) else "")
    match = re.search(r"(\d{2}(?:\.\d)?)\s*(?:英寸|吋|寸)", text)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value if 10 <= value <= 120 else None


def confidence_for_candidate(candidate: dict[str, Any]) -> float:
    """Heuristic evidence completeness score, not a probability of quality."""

    score = 0.35
    if get_path(candidate, "price_status") == "visible":
        score += 0.20
    if not is_missing(get_path(candidate, "detail.specs")):
        score += 0.15
    if not is_missing(get_path(candidate, "shop_name")):
        score += 0.10
    if not is_missing(get_path(candidate, "sales")):
        score += 0.05
    if not is_missing(get_path(candidate, "url")):
        score += 0.05
    if (get_path(candidate, "reviews.sample_size") or 0) > 0:
        score += 0.05
    if get_path(candidate, "evidence") or get_path(candidate, "browser_evidence"):
        score += 0.05
    return round(min(0.95, score), 2)


def extract_error(result: Any) -> dict[str, Any] | None:
    """Normalize the MCP's several error shapes into one structure."""

    if not isinstance(result, dict):
        return None
    if result.get("ok") is False:
        error = result.get("error")
        if isinstance(error, dict):
            return {
                "code": str(error.get("code") or "UNKNOWN"),
                "message": str(error.get("message") or result.get("message") or "MCP error"),
                "retryable": bool(error.get("retryable", False)),
                "hint": str(error.get("hint") or ""),
            }
        return {
            "code": "UNKNOWN",
            "message": str(error or result.get("message") or "MCP error"),
            "retryable": False,
            "hint": "",
        }
    error = result.get("error")
    if isinstance(error, dict):
        return {
            "code": str(error.get("code") or "UNKNOWN"),
            "message": str(error.get("message") or "MCP error"),
            "retryable": bool(error.get("retryable", False)),
            "hint": str(error.get("hint") or ""),
        }
    if isinstance(error, str) and error.strip():
        return {"code": "UNKNOWN", "message": error.strip(), "retryable": False, "hint": ""}
    ret = result.get("ret")
    if isinstance(ret, list) and ret and not any("SUCCESS" in str(item) for item in ret):
        return {
            "code": "PLATFORM_ERROR",
            "message": "::".join(str(item) for item in ret),
            "retryable": False,
            "hint": "",
        }
    return None


def load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def save_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def unique_values(values: Iterable[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result
