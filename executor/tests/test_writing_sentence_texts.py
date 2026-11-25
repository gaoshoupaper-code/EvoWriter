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
    assert "每次回复仅调用一个写作工具" in regimen and "该句自成一段" in regimen
    assert regimen.index("`writing_intent`") < regimen.index("`text`")
    assert "等回执回来才想下一句" in regimen
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


# REQ-20261125-210621 FR-001 的定稿文案：模型每次调用读到的说明必须与这里逐字一致（AC-001）。
_DESCRIPTIONS = {
    "write_action": "写一句人物主动做出的外部可见动作（肢体动作、位移、与人/物的交互、刻意表露的神态）。\n"
                    "\n"
                    "写法：写清主体、方式与落点（落在哪个人或物的具体部位）；一句只落一个动作。"
                    "重要动作必须拆成连续多次调用逐步推进（如先靠近、再换姿势、最后接触），禁止把多个步骤挤进同一句。\n"
                    "\n"
                    "边界：受刚发生的事触动而起的非自主身体变化（手抖、脸红、一僵）改用 write_reaction；"
                    "说话改用 write_dialogue。",
    "write_dialogue": "写一句人物说出声的话，原话放进引号。\n"
                      "\n"
                      "写法：一句只表达一层意思；说话提示保持极简，仅交代说话人或声音/语气（如「他压低声音说」），"
                      "禁止在提示中夹带明显动作、神态或心理描写。\n"
                      "\n"
                      "边界：伴随的动作或神态拆出去另调用 write_action / write_reaction；"
                      "未说出口的想法改用 write_reaction。",
    "write_sensory": "写一句外界环境或物理接触带来的直接五感（视觉细节、声响、气味、味觉、温度与触感）。\n"
                     "\n"
                     "写法：写清具体来源、空间位置和质感/强弱；"
                     "只写客观感知，不掺主观评价（如「很难受」「很诡异」）或心理推演。\n"
                     "\n"
                     "边界：他人的动作直接用 write_action 写，不要套「看见/听到某人……」；"
                     "受情绪或事件刺激引发的自身生理反应（如喉咙发紧、心跳加快）改用 write_reaction。",
    "write_reaction": "写一句人物被刚发生的事触动后的即时回应，包括非自主身体变化或脑中闪过的念头。\n"
                      "\n"
                      "写法：身体反应写具体部位和变化（如指节发白、呼吸一滞）；"
                      "心理反应写具体念头，不直接贴情绪标签（如「他很生气」「她有些委屈」）。"
                      "一次只写单一反应，不把身体变化与内心想法混在同一句。\n"
                      "\n"
                      "边界：人物有意发起的动作或刻意神态改用 write_action；说出声的话改用 write_dialogue。",
    "write_narration": "写一句讲述人视角的过渡或交代（时间跳跃、场景切换、背景补充、非重点过程的概述）。\n"
                       "\n"
                       "写法：一句只交代一件事，保持克制，不把多个事件或因果压进同一长句。\n"
                       "\n"
                       "边界：当前场景中正在发生、看得见听得着的具体过程，必须用另外四个工具逐句写，禁止用叙述一笔带过。",
}


@pytest.mark.parametrize("name", sorted(SENTENCE_TOOLS))
def test_every_sentence_tool_reads_the_approved_description(tmp_path, name):
    tool = importlib.import_module(f"harness_current.tools.{name}").build(SimpleNamespace(workspace_path=tmp_path))
    assert tool.description == _DESCRIPTIONS[name]
    assert list(tool.args) == ["writing_intent", "text"]
    assert tool.args_schema.model_json_schema()["required"] == ["writing_intent", "text"]
    assert tool.args["text"]["description"] == "这一句的正文：一个完整的句子，不带换行"
