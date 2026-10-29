from __future__ import annotations

import json

import pytest

from conftest import FIXTURES
from freeze_plan import freeze, validate_plan
from render_confirmation import render_confirmation


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_confirmation_sheet_contains_requirements_dimensions_and_assumptions():
    text = render_confirmation(_load("brief.json"), _load("scorecard.json"))
    assert "筛选（must）" in text
    assert "需求匹配维度（should）" in text
    assert "评比维度" in text
    assert "确认这版" in text


def test_plan_validation_requires_groups_and_requirements():
    brief = _load("brief.json")
    scorecard = _load("scorecard.json")
    scorecard.pop("stages")
    errors = validate_plan(brief, scorecard)
    assert any("未分组" in error or "stages" in error for error in errors)

    brief.pop("requirements")
    errors = validate_plan(brief, _load("scorecard.json"))
    assert any("requirements" in error for error in errors)


def test_freeze_records_revision_and_confirmation(tmp_path):
    brief_path = tmp_path / "brief.json"
    scorecard_path = tmp_path / "scorecard.json"
    brief = _load("brief.json")
    scorecard = _load("scorecard.json")
    brief["confirmed"] = {"by_user": True, "at": "2026-10-29T08:00:00+08:00", "revision": 1}
    scorecard["confirmed"] = {"by_user": True, "at": "2026-10-29T08:00:00+08:00", "revision": 1}
    brief_path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    scorecard_path.write_text(json.dumps(scorecard, ensure_ascii=False), encoding="utf-8")

    result = freeze(brief_path, scorecard_path, by_user=True, changes=["确认维度"])
    assert result["brief"]["revision"] == 2
    assert result["brief"]["by_user"] is True

    saved = json.loads(brief_path.read_text(encoding="utf-8"))
    assert saved["confirmed"]["changes"] == ["确认维度"]


def test_freeze_rejects_invalid_weight_sum(tmp_path):
    brief_path = tmp_path / "brief.json"
    scorecard_path = tmp_path / "scorecard.json"
    brief = _load("brief.json")
    scorecard = _load("scorecard.json")
    scorecard["dimensions"][0]["weight"] = 0.4  # value group no longer sums to 1
    brief_path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    scorecard_path.write_text(json.dumps(scorecard, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError):
        freeze(brief_path, scorecard_path)
