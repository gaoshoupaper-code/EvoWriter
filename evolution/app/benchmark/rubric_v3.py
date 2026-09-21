"""评测 rubric v3 —— 大纲五维 + 交付完整规则项（REQ-20260919-172934 / DEC-009/010/011）。

锚点与词表按 REQ-20260920-150253（FR-003）重写：
- 需求兑现改为「承诺点制」——只判 demand「承诺点（不可漂移项）」小节的兑现
  与漂移，留白部分不计入该维（DEC-008）；
- 其余四维适配留白输入（semi/minimal 档的补全质量由设定自洽/人物/情节/节奏兜底）；
- 节奏结构的「爽点」口径按题材基线泛化（爽文看爽点密度，治愈向看温情密度）。

与 eval_agent/rubrics/xianxia.py（v2-28dim，随生产评估链路休眠）的关系：
评测系统（benchmark）是唯一评分主链路，本模块是评测的唯一评分标准。

维度集（DEC-009）：5 主观维（1-5 整数，0=无法判断）+ 1 规则项（代码判）。
评分范式（DEC-010）：每维 1/3/5 档锚点 + 闭合缺陷标签词表；分数 ≤2 必须挂词表内标签。

锚点状态（DEC-011）：本版为按新标准重写的待终审稿，用户改定前
CALIBRATION 与 DRAFT 状态保持 uncalibrated/draft——未改定不得建正式基线。
"""
from __future__ import annotations

from typing import Any

RUBRIC_VERSION = "v3-outline-5dim-uncalibrated"
CALIBRATION_STATUS = "uncalibrated"
ANCHOR_DRAFT_STATUS = "draft"  # 待终审稿；用户改定后置 "user_finalized"

# 低分线：≤2 必须挂缺陷标签（DEC-010）
LOW_SCORE_THRESHOLD = 2

# ── 五个主观维度 ────────────────────────────────────────────
# anchors 只写可判定的行为特征（需求风险处置约束），不写「好大纲像什么」。
# defect_tags 是闭合词表：judge 不得输出词表外标签（scorer 强制校验）。

DIMENSIONS: list[dict[str, Any]] = [
    {
        "key": "需求兑现",
        "question": "创作需求中「承诺点（不可漂移项）」小节所列各条，在最终大纲中是否全部兑现、无未经说明的漂移？（需求未给定/留白的部分不计入本维）",
        "anchors": {
            "1": "承诺点被丢弃、替换或与大纲明显矛盾（如题材错位、金手指内核被改写、主角身份与设定不符）",
            "3": "承诺点基本保留，但个别条目部分落空或被未经说明地弱化（如代价减轻、篇幅档位或配比明显偏离）",
            "5": "每条承诺点都能在大纲中指认兑现位置，无未经说明的漂移；题材与子流派准确",
        },
        "defect_tags": ["承诺点丢失", "承诺点弱化", "题材错位", "金手指改写", "主角设定不符", "结构配比偏离"],
    },
    {
        "key": "设定自洽",
        "question": "世界观与力量体系的规则是否内部一致、跨三件套无矛盾、可稳定推演？需求留白处补全的设定与给定内核是否衔接自洽？",
        "anchors": {
            "1": "规则互相矛盾或可被随意改写（能力成为万能解法），三件套间存在实质冲突",
            "3": "核心规则可用，但层级、边界或代价仍有模糊处，三件套有局部不一致",
            "5": "规则、限制、代价形成闭环、可稳定推演，主线/人物/世界观三件套无实质冲突，补全设定与给定内核（如有）无缝衔接",
        },
        "defect_tags": ["规则冲突", "能力无代价", "体系断层", "跨件套矛盾", "补全失洽"],
    },
    {
        "key": "人物塑造",
        "question": "主角动机、缺陷与弧线是否清晰，配角是否有明确功能，关系是否推动情节？",
        "anchors": {
            "1": "角色缺乏动机或纯属功能工具，关系不产生冲突或选择",
            "3": "主角动机清楚、关键配角可区分，但弧线平淡、关系变化有限",
            "5": "主角有可感弧线（欲望-阻力-变化），配角目标与主角相互作用，关系持续产生冲突与合作",
        },
        "defect_tags": ["动机缺失", "弧线缺失", "工具人配角", "关系静态", "人设扁平"],
    },
    {
        "key": "情节构造",
        "question": "主线因果链是否完整有吸引力，成长线（玄幻）是否递进合理，走向是否可信？",
        "anchors": {
            "1": "主线无因果或断裂，冲突可被轻易化解，成长无递进",
            "3": "主线完整但平铺直叙，冲突强度或成长台阶区分不足，局部依赖巧合",
            "5": "因果链环环相扣，目标-阻力-代价逐级升级，关键转折有铺垫、结局有回收，核心卖点贯穿始终",
        },
        "defect_tags": ["主线断裂", "冲突失效", "巧合推进", "成长无递进", "卖点不贯穿", "结局潦草"],
    },
    {
        "key": "节奏结构",
        "question": "蓝图级结构是否清晰，张弛节奏与关键转折、情绪点/爽点分布是否有设计感？（情绪点按题材基线：爽文看爽点密度，治愈/日常向看温情密度）",
        "anchors": {
            "1": "无结构可言或节奏全篇均质，无转折设计",
            "3": "有基本结构框架，但高潮前置、中段松散或情绪点/爽点分布无规律",
            "5": "阶段划分清晰，铺垫-爆发-回落有节奏，转折与情绪点/爽点分布有明确设计且密度按题材基线得当",
        },
        "defect_tags": ["结构混乱", "节奏均质", "中段松散", "高潮缺失", "情绪点稀薄"],
    },
]

DIMENSION_KEYS = [d["key"] for d in DIMENSIONS]

# 全部合法标签的闭合集（scorer 校验 judge 输出用）
ALL_DEFECT_TAGS: set[str] = {
    tag for d in DIMENSIONS for tag in d["defect_tags"]
}

# ── 规则项：交付完整（代码判定，不走 LLM）──────────────────
# FR-002 ③：三件套齐全、结构完整可解析。判定实现在 scorer.py。

RULE_DELIVERY_COMPLETE = {
    "key": "交付完整",
    "description": "大纲三件套（主线/人物/世界观）文件齐全、内容非空且非占位符、结构可解析",
}


# ── judge prompt 构建 ───────────────────────────────────────


def build_judge_system_prompt() -> str:
    """评分任务说明 + 维度锚点表 + 词表约束 + 输出格式（system prompt）。"""
    dim_blocks: list[str] = []
    for d in DIMENSIONS:
        anchors = "\n".join(f"    {level} 分：{text}" for level, text in d["anchors"].items())
        tags = "、".join(d["defect_tags"])
        dim_blocks.append(
            f"### 维度：{d['key']}\n"
            f"  判定问题：{d['question']}\n"
            f"  档位锚点：\n{anchors}\n"
            f"  缺陷标签词表（只能从此列表选择）：{tags}"
        )
    dims_text = "\n\n".join(dim_blocks)
    keys_text = "、".join(DIMENSION_KEYS)

    return f"""你是玄幻小说大纲的质量评审。对给定「创作需求 + 大纲三件套（主线/人物/世界观）」按以下 5 个维度打分。

{dims_text}

## 评分规则
- 每维打 1-5 整数分；证据不足以判断时打 0（无法判断），不得猜测。
- 档位语义：1/3/5 为锚定档，2/4 为相邻档之间的中间态。
- 「需求兑现」只依据需求中「承诺点（不可漂移项）」小节与题材判定；需求未给定（留白）的部分不计入该维。
- 分数 ≤2 的维度，必须从该维词表中选择缺陷标签（可多个），并给一句理由。
- 分数 ≥3 的维度，tags 给空数组，reason 可省略。
- 依据三件套与需求的可观察内容判定，不凭文风印象加分。

## 输出格式（只输出 JSON，不要输出其他内容）
```json
{{
  "scores": {{"{DIMENSION_KEYS[0]}": 0, "{DIMENSION_KEYS[1]}": 0, "{DIMENSION_KEYS[2]}": 0, "{DIMENSION_KEYS[3]}": 0, "{DIMENSION_KEYS[4]}": 0}},
  "tags": {{"维度名": ["标签", ...]}},
  "reasons": {{"维度名": "一句话理由"}}
}}
```
5 个维度（{keys_text}）的 scores 必须全部出现。"""


def build_judge_user_prompt(demand_md: str, deliveries: dict[str, str]) -> str:
    """评分输入：创作需求 + 三件套正文。

    deliveries: {logical_key 或展示名: 内容}，三件套按固定顺序拼接。
    """
    parts = [f"## 创作需求\n\n{demand_md}"]
    artifacts = "\n\n---\n\n".join(
        f"## {name}\n\n{content}" for name, content in deliveries.items()
    )
    parts.append(f"## 待评大纲（三件套）\n\n{artifacts}")
    return "\n\n".join(parts)


__all__ = [
    "RUBRIC_VERSION",
    "CALIBRATION_STATUS",
    "ANCHOR_DRAFT_STATUS",
    "LOW_SCORE_THRESHOLD",
    "DIMENSIONS",
    "DIMENSION_KEYS",
    "ALL_DEFECT_TAGS",
    "RULE_DELIVERY_COMPLETE",
    "build_judge_system_prompt",
    "build_judge_user_prompt",
]
