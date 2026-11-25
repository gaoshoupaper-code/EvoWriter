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


_ENDING = "一次只写一个完整的句子，这一句自成一段；等工具返回后再写下一句。"
# REQ-20261125-210621 FR-001 的定稿文案：模型每次调用读到的说明必须与这里逐字一致（AC-001）。
_DESCRIPTIONS = {
    "write_action": "写一句「动作」：人物主动做了什么，旁人看得见，包括手势、走动和神情。\n"
                    "一句只写一个动作：谁、怎么做、落在哪个人或物的哪个部位。\n"
                    "要紧的动作写出过程：先怎么靠近、怎么换姿势，再落下去。\n"
                    "被触动才起的身体变化（手一抖、脸一红）用 write_reaction。\n" + _ENDING,
    "write_dialogue": "写一句「语言」：人物说出声的话，原话放进引号。\n"
                      "一句只说一层意思；可以带一个简短的说话提示，如谁说的、什么语气。\n"
                      "没说出声的想法用 write_reaction。\n" + _ENDING,
    "write_sensory": "写一句「感官」：人物直接感到的印象，看到的、听到的、闻到的、尝到的、触到的，不掺想法。\n"
                     "写具体的来源、位置和强弱，不写「很难受」这类评价。\n"
                     "别人做了什么，照实写成 write_action，不套「看见」「听到」。\n" + _ENDING,
    "write_reaction": "写一句「反应」：人物被刚发生的事触动后的回应，身体的和心里的都算。\n"
                      "身体反应写具体部位和变化，如手一抖、喉咙发紧。\n"
                      "心里的反应写冒出的念头，不直接贴情绪标签。\n"
                      "有意去做的事用 write_action；说出声的话用 write_dialogue。\n" + _ENDING,
    "write_narration": "写一句「叙述」：讲述人交代的内容，包括过渡、时间跳跃、背景和概述。\n"
                       "一句交代一件事，不把几件事压在一句里。\n"
                       "正在发生、看得见听得着的事，用另外四个工具逐句写，不用叙述带过。\n" + _ENDING,
}


@pytest.mark.parametrize("name", sorted(SENTENCE_TOOLS))
def test_every_sentence_tool_reads_the_approved_description(tmp_path, name):
    tool = importlib.import_module(f"harness_current.tools.{name}").build(SimpleNamespace(workspace_path=tmp_path))
    assert tool.description == _DESCRIPTIONS[name]
    assert list(tool.args) == ["text"]
    assert tool.args["text"]["description"] == "这一句的正文：一个完整的句子，不带换行"
