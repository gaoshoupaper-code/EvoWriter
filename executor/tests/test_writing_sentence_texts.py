"""规程文字随逐段写作同步改写。"""
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


def test_regimen_describes_writing_by_paragraph_and_no_longer_by_batch(regimen):
    assert all(tool in regimen for tool in DRAFT_TOOLS)
    assert "每次只调用一个写作工具" in regimen and "正文只收录 `text`" in regimen
    assert regimen.index("`writing_intent`") < regimen.index("`text`")
    assert "逐段推进" in regimen and "再决定下一段" in regimen
    assert not [word for word in _BATCH_DRAFTING if word in regimen]


def test_regimen_keeps_the_forms_a_choice_and_never_a_quota(regimen):
    assert "不按固定顺序轮换，也不凑数" in regimen
    assert "信息" in regimen and "铺垫" in regimen and "因果" in regimen and "过渡" in regimen


def test_regimen_states_the_pacing_floorline(regimen):
    assert "节奏与连贯底线" in regimen
    assert "write_setup` / `write_info" in regimen and "给足关键在场者的反应或克制理由" in regimen


def test_regimen_tells_the_model_where_paragraphs_live_before_the_chapter_is_finalized(regimen):
    assert HEADER_TOOL in regimen and "初稿" in regimen
    assert "只看工具回执" in regimen
    assert "整章重写" in regimen and "`full`" in regimen


def test_review_regimen_no_longer_measures_against_a_batch_length():
    review = (_HARNESS_DIR / "prompts" / "review.md").read_text(encoding="utf-8")
    assert "批长" not in review and "普通批" not in review
    assert "不能仅因超过篇幅预算或原章长就建议压缩必需过程" in review


# 模型每次调用读到的说明必须与这里逐字一致。
_DESCRIPTIONS = {
    "write_info": "写一段用于确立客观坐标的场景信息（时间、地点、空间布局、相对距离、视线与遮挡、在场/缺席人员及关键物件位置），让读者脑中有稳定画面，不迷路。\n"
                  "\n"
                  "适用场景：\n"
                  "- 新场景开场，或人物进入新空间时，交代环境边界、大小、布局与感官锚点。\n"
                  "- 关键互动开始前，补清人物之间的相对位置、距离、朝向、遮挡物或道具所在。\n"
                  "- 场景中静态状态发生显性清单变化时（如明确谁已入席、谁仍缺席）。\n"
                  "\n"
                  "写法规范：\n"
                  "- 写客观可感知的具体细节（视觉、声响、光线、温度、材质与方位关系），不写空洞概括（如「大殿很气派」），不掺人物主观评价。\n"
                  "- 重点交代「空间关系」而非零散摆设：说清人/物彼此离多远、隔着什么、能不能看清或够得着。\n"
                  "- 篇幅克制在 2–4 句，自然融入当下场景视角，禁止写成干巴巴的说明书或建筑清单。\n"
                  "- 主语明确、方位前后一致，读者一遍即可在脑中还原站位。\n"
                  "\n"
                  "边界：\n"
                  "- 若细节的核心目的是埋设定规则、立人物性格、造压迫氛围或给后文转折留依据，改用 write_setup。\n"
                  "- 若涉及人物主动发起冲突、对话交锋或引发局势变化，改用 write_causal。\n"
                  "- 若只需简短切换时空或略过无关路程，改用 write_transition。",
    "write_setup": "写一段用于建立叙事前提的铺垫内容（人物性格与立场、势力格局与筹码、特殊/超常规则、压迫氛围或伏笔），让后续的关键转折与人物选择合情合理、有据可依。\n"
                   "\n"
                   "适用场景：\n"
                   "- 章纲或后续剧情包含重大结果、反转、权力压制或非常规机制，动笔前需先立住「它凭什么能成立」的前置条件。\n"
                   "- 冲突爆发前，交代关键角色的动机、顾虑、底牌、弱点或彼此的力量悬殊。\n"
                   "- 需要蓄积情绪压迫感、异常征兆或心理暗流，为下一阶段的爆发蓄力。\n"
                   "\n"
                   "写法规范：\n"
                   "- 优先「展示」而非干讲设定：通过具体细节、反常迹象、人物微表态、只言片语或克制的背景补白，把规则、筹码和氛围自然立起来。\n"
                   "- 每段聚焦 1 个核心前提或伏笔（2–5 句），严禁堆砌与本章无关的世界观百科。\n"
                   "- 写性格与情绪时落到具体言行、神态或细节征兆，禁止直接贴抽象标签（如「他心机很深」「气氛非常压抑」）。\n"
                   "- 只埋后文真正会用到的依据，确保与已有设定和章纲不冲突。\n"
                   "\n"
                   "边界：\n"
                   "- 纯交代客观时间、地点、距离与物理布局（不涉及规则、立场或伏笔），改用 write_info。\n"
                   "- 当前提已被实际触发、演变为正面行动/对白交锋与后果落地时，改用 write_causal。\n"
                   "- 若前提已足够，只需在两个事件间做节奏缓冲或时间衔接，改用 write_transition。",
    "write_causal": "写一段真正推动剧情的核心因果单元，完整覆盖「触发（动作/对白/突发变故）→ 关键方与在场者反应 → 状态或局势变化」的最小因果闭环。\n"
                    "\n"
                    "适用场景：\n"
                    "- 推进章纲中的核心事件、冲突交锋、施压与反抗、对白博弈、关键信息揭露或状态改变。\n"
                    "- 任何会改变人物位置、控制权、关系、认知或局势走向的实质性推进。\n"
                    "\n"
                    "写法规范：\n"
                    "- 因果完整：段内必须交代清楚「谁做了/说了什么 → 承受者及关键在场者如何回应 → 带来了什么具体变化」，避免只写单方施为、忽略后果与现场反馈。\n"
                    "- 群像不失声：当重大冲突、羞辱、威慑或局势转折在多人面前发生时，必须交代关键旁观者的真实反应、立场权衡，或「为何选择沉默/不介入」的具体理由，禁止只留空壳生理动作（如全员只剩停住呼吸、沉默）。\n"
                    "- 动作与空间严密：写清施动者、方式与具体落点，衔接前文已确立的站位、朝向与距离，不把尚未发生的位移或接触写成既成状态；重要交锋写足细节，但不把机械微动作反复拆碎拖垮节奏。\n"
                    "- 对白与反应具体：原话放进引号，说话提示简洁；身体反应写具体部位与变化，内心反应写具体念头或权衡，不贴情绪标签。\n"
                    "- 清晰可读：单段聚焦 1 个清晰的因果回合（2–6 句），不把多个大转折硬塞进一大段；若段内包含不同角色的即时问答或动作—对白切分，可在 text 内用换行自然分段，保证一遍读懂、不混主语。\n"
                    "\n"
                    "边界：\n"
                    "- 若本回合依赖的规则、筹码或空间位置尚未交代，先调 write_setup 或 write_info 补齐前提，严禁在因果段里硬跳结果。\n"
                    "- 无新因果推进的例行位移、重复过程或短暂留白，改用 write_transition 压缩或缓冲，禁止用 write_causal 刷机械动作。",
    "write_transition": "写一段连接两个因果节点的过渡内容，负责快进压缩非重点过程、缓冲高潮张力，或干净地切换时间、空间与叙事焦点，不改变核心大因果。\n"
                        "\n"
                        "适用场景：\n"
                        "- 两个核心因果事件之间需要时空衔接、视线/焦点转移，或短暂的留白与余波沉淀。\n"
                        "- 存在非重点的例行位移、等待、重复性动作或时间跨度，需要一两句干脆带过以保持节奏紧凑。\n"
                        "- 剧烈冲突刚结束或即将升级前，用克制的停顿维持时空连续性与叙事张力。\n"
                        "\n"
                        "写法规范：\n"
                        "- 明确区分两种用法，按当前节奏二选一：\n"
                        "  1. 快进压缩：用 1–2 句干净利落地概括重复过程或无关位移，杜绝机械拆解肢体微动作。\n"
                        "  2. 缓冲蓄势：用 1–3 句承接上一段落下的余波、静默或焦点移动，把镜头自然引向下一段。\n"
                        "- 承上启下明确：交代清楚从什么状态/时间/位置切走，落到哪里，避免跳跃突兀。\n"
                        "- 篇幅精短（1–3 句），克制不注水。\n"
                        "\n"
                        "边界：\n"
                        "- 严禁用 write_transition 一笔带过章纲要求的核心事件、关键冲突、关键展示过程或重要后果（这些必须用 write_causal 写实）。\n"
                        "- 切换到全新场地且后续互动强依赖具体布局与距离时，需紧接调用 write_info 锚定空间。",
}


@pytest.mark.parametrize("name", sorted(SENTENCE_TOOLS))
def test_every_paragraph_tool_reads_the_approved_description(tmp_path, name):
    tool = importlib.import_module(f"harness_current.tools.{name}").build(SimpleNamespace(workspace_path=tmp_path))
    assert tool.description == _DESCRIPTIONS[name]
    assert list(tool.args) == ["writing_intent", "text"]
    assert tool.args_schema.model_json_schema()["required"] == ["writing_intent", "text"]
    assert tool.args["text"]["description"] == "这一段的成段正文"
    assert "格式简写" in tool.args["writing_intent"]["description"]


def test_writing_intent_guide_matches_the_tool(tmp_path):
    causal = importlib.import_module("harness_current.tools.write_causal").build(
        SimpleNamespace(workspace_path=tmp_path))
    assert "触发动作/话语 → 目标与在场者反应 → 局势/状态变化" in causal.args["writing_intent"]["description"]
    info = importlib.import_module("harness_current.tools.write_info").build(
        SimpleNamespace(workspace_path=tmp_path))
    assert "确立的硬坐标 → 为后续哪类互动/视线提供依据" in info.args["writing_intent"]["description"]
