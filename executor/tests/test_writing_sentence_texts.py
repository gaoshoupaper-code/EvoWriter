"""规程文字随逐句写作同步改写（REQ-20261125-154544 FR-008、DEC-010、DEC-016；AC-009）。"""
from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.platform.agent.loader import load_package
from contracts.creative_harness import HEADER_TOOL, SENTENCE_TOOLS
from contracts.sentence_draft import DRAFT_TOOLS

_HARNESS_DIR = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"
load_package(_HARNESS_DIR)
_BATCH_DRAFTING = ("每批", "一批", "首批", "append", "unit_kind", "skill_scene", "整场", "300–400", "450 字")


@pytest.fixture(scope="module")
def regimen() -> str:
    return (_HARNESS_DIR / "prompts" / "creative_text.md").read_text(encoding="utf-8")


def test_regimen_describes_writing_by_sentence_and_no_longer_by_batch(regimen):
    assert all(tool in regimen for tool in DRAFT_TOOLS)
    assert "一次回复只写一句" in regimen and "每句自成一段" in regimen
    assert not [word for word in _BATCH_DRAFTING if word in regimen]


def test_regimen_keeps_the_forms_a_choice_and_never_a_quota(regimen):
    assert "不按固定顺序轮换，也不凑数" in regimen
    assert "叙述" in regimen and "过渡" in regimen


def test_regimen_tells_the_model_where_sentences_live_before_the_chapter_is_finalized(regimen):
    assert HEADER_TOOL in regimen and "初稿" in regimen
    assert "只看工具回执" in regimen
    assert "整章重写" in regimen and "`full`" in regimen


def test_review_regimen_no_longer_measures_against_a_batch_length():
    review = (_HARNESS_DIR / "prompts" / "review.md").read_text(encoding="utf-8")
    assert "批长" not in review and "普通批" not in review
    assert "不能仅因超过篇幅预算或原章长就建议压缩必需过程" in review


@pytest.mark.parametrize("name", sorted(SENTENCE_TOOLS))
def test_every_sentence_tool_explains_itself_in_one_line(tmp_path, name):
    tool = importlib.import_module(f"harness_current.tools.{name}").build(SimpleNamespace(workspace_path=tmp_path))
    assert tool.description.startswith("写下一句")
    assert tool.description.endswith("一次只写一个完整的句子，这一句自成一段；等工具返回后再写下一句。")
    assert list(tool.args) == ["text"]
