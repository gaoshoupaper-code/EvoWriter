"""Independent immutable rubric revisions for the three target task types."""
from __future__ import annotations

import threading

from app.benchmark.rubric_v3 import DISCIPLINE_RULES
from app.experiments import chapter_rubric, repo
from app.experiments.schemas import RuleCreate
from contracts.target_tasks import configured_task_type

_lock = threading.RLock()
PROFILE = "stage-eval-v2"
TEXT_PROFILE = "chapter-text-outline-v3"
_LEGACY_TEXT_KEYS = {"事件因果逻辑", "人物行为逻辑", "章纲兑现", "语言清晰度"}
_DIMENSIONS = {
    "master_outline": [
        ("主题清晰度", "总纲是否将用户意图落实为能贯穿全书的主题？",
         ["缺少主题或与需求相反", "只有模糊口号", "主题清楚但联系剧情不足", "主题和主要事件对应",
          "主题明确并由人物选择与结局具体支撑"]),
        ("主角目标与冲突", "主角的目标、对立力量和失败代价是否明确？",
         ["缺少可辨目标", "有目标但没有实质阻力", "目标与冲突基本明确", "行动动机及失败代价清楚",
          "目标、阻力、代价与关键选择紧密对应"]),
        ("主线转折因果", "关键转折能否由人物的选择及后果推出？",
         ["事件互不关联", "多处转折依赖巧合", "主线成立但关键连接欠充分", "关键选择和转折有据",
          "转折前因和后果可逐一追溯，并为结局铺垫"]),
        ("可分卷性", "能否据此划分各卷的推进任务和交接点？",
         ["无法识别分卷任务", "分卷仅有标题", "可分卷但目标重叠", "各卷功能与交接清楚",
          "各卷的起点、冲突、结果及交接均可用于下一步规划"]),
    ],
    "volume_outlines": [
        ("需求兑现", "需求中的题材、核心设定、主角目标及明确限制，是否落实在整本卷纲的具体安排中？", [
            "卷纲改换了核心题材、设定或主角目标，与需求中不可变更的承诺相反。",
            "至少一条硬性承诺整条落空，或至少两条承诺被明显削弱，能指认缺失的位置。",
            "承诺均被提及，但有一条只写成口号或待补计划，没有对应的事件与结果安排。",
            "每条承诺都有实际兑现位置，仅一处措辞含混；事件安排仍与需求一致。",
            "全部明确承诺都能逐条对应到目标卷纲中的事件和结果，无漂移、弱化或占位。",
        ]),
        ("主线因果", "全书目标、阻力、人物选择与结果，是否构成有依据的主线因果链？", [
            "主线主要事件彼此无因果关系，最终结果无法由此前行动推出。",
            "至少两个关键转折依赖无铺垫的巧合，或主角选择与主线结果明显无关。",
            "主线大体能串联，但有一个关键转折缺少动机、条件或后果依据。",
            "关键选择和结果均可追溯，仅一处次要因果连接解释略省，不妨碍理解主线。",
            "每个关键转折都有既有目标、阻力和选择支撑，结果反过来改变后续行动条件。",
        ]),
        ("章间因果", "卷纲中规划的相邻章节，是否以前章后果推动后章行动，跨卷衔接是否成立？", [
            "章节规划只是互不相关的事件清单，前章结尾无法解释后章为何发生。",
            "多处相邻章节出现因果跳步、后果消失或重复完成同一事件。",
            "多数章节前后相承，但有一处关键章节或跨卷连接缺少必要过渡。",
            "全部关键章节及跨卷连接均成立，仅一处次要承接写得简略，前因仍可确认。",
            "相邻章节的行动条件、选择及后果连续可追溯，跨卷连接明确，无跳步或重复推进。",
        ]),
        ("分卷节奏", "各卷目标、关键推进、高潮与收束是否分工清楚，符合整书阶段安排？", [
            "各卷没有可辨认的阶段目标，主要冲突无发展就直接结束，或一直重复同一局面。",
            "多卷功能重复，或关键高潮与收束挤在极少章节，前面大量规划没有推进。",
            "各卷已有目标和转折，但有一卷明显拖延、过密，或阶段结果未有效衔接下一卷。",
            "各卷推进与收束配合合理，仅一处局部章节分配稍急或稍缓，不影响整书起伏。",
            "每卷承担清晰且不同的推进任务，张弛、高潮和收束有章可查，整书节奏无明显失衡。",
        ]),
    ],
    "chapter_outlines": [
        ("卷纲兑现", "指定章的指令卡，是否落实所属卷纲为本章安排的事件、目标和限制？", [
            "指令卡改变了指定章的核心任务或结果方向，实际写成另一章的内容。",
            "卷纲明确安排的一项核心事件整项缺失，或至少两项关键限制被改写。",
            "本章事件都被提到，但一项关键事件停留在概述，未落实行动或状态变化。",
            "本章安排及限制均落实，仅一处次要细节表述含混，不影响卷纲要求。",
            "卷纲对指定章的事件、目标和限制均能对应到指令卡的具体行动与结果，无越界推进。",
        ]),
        ("状态连续", "指令卡中的适用时间、地点、物品、认知及能力状态，是否承接固定上游与前章？", [
            "本章起始状态与上游或前章直接冲突，主要行动依赖角色不可能拥有的物品、知识或能力。",
            "至少两处状态无来源地改变，或一处关键状态跳变使本章行动无法成立。",
            "关键起止状态大体连续，但一项状态变化缺少过程或与前章钩子的衔接说明。",
            "所有关键状态都有来源和变化过程，仅一处次要状态交代略省，不影响本章行动。",
            "全部适用起始状态可追溯，状态变化与行动对应，前章钩子承接和章末状态均一致。",
        ]),
        ("章内节奏", "指定章内部的铺垫、冲突推进、选择和结尾变化，是否分配得当并形成起伏？", [
            "本章只有设定说明或结论罗列，缺少实际冲突推进与可辨认的结尾变化。",
            "关键选择缺少铺垫，或大量重复信息挤占本章主要冲突，转折和结尾仓促跳过。",
            "本章有完整推进链，但一段明显拖沓或一个关键转折承载不足，影响起伏。",
            "铺垫、推进、选择和结尾变化配比合理，仅一个次要节拍稍急或稍缓。",
            "每个章内节拍都有推进作用，重要选择得到充分承载，信息释放与结尾变化自然相接。",
        ]),
        ("可直接执笔", "指定章指令卡是否按现有 Prompt 提供足够具体的目标、行动、冲突及变化，能直接写正文？", [
            "指令卡只有主题或情绪口号，缺少谁做什么、为何受阻和结果如何，无法直接执笔。",
            "核心行动、阻力或结果至少缺一项，必须重新构思本章主要剧情才能写正文。",
            "主要剧情已可理解，但一处关键行动、冲突解决或状态变化仍需作者补造。",
            "现有规程要求的适用字段具体且齐全，仅一处次要动作细节需要自然展开。",
            "目标、阻力、选择、行动与结果均具体可执行，适用字段相互一致，无需补造关键剧情。",
        ]),
    ],
    "chapter_text": [
        ("章纲兑现", "最终正文是否实际写出目标章纲规定的主要事件、限制和章末变化？", [
            "正文写成不同的章节任务，核心事件或章末结果与目标章纲相反。",
            "至少一个核心事件完全未写出，或多项章纲限制被擅自取消。",
            "主要事件均在，但一项关键过程只被概述带过，或一处指定变化没有真正发生。",
            "主要事件、限制和章末变化均实际落地，仅一处次要细节与章纲略有偏差。",
            "章纲承诺均有正文段落对应，关键过程真实展开，起止状态和结尾变化完全一致。",
        ]),
        ("逻辑连续", "正文动作、时空、物品和人物认知是否连续，选择与结果是否符合固定上下文？", [
            "主要行动在既定时空或认知条件下不可能发生，核心事件链无法成立。",
            "多处人物瞬移、物品凭空出现或信息提前获知，或一处矛盾推翻本章关键结果。",
            "主要行动链成立，但有一处需要补充时间、位置、物品来源或认知过程才能解释。",
            "关键行动与结果均有连续依据，仅一处次要动作过渡略省，不影响事件判断。",
            "所有关键行动的时空、条件和认知都有依据，章纲到正文及章内因果连续，无可指认矛盾。",
        ]),
        ("人物与对话", "人物行动是否符合既定身份和动机，对话是否具有角色差异并推动当前冲突？", [
            "关键人物无理由地违背核心动机，对话无法服务实际场景，人物行为使剧情失真。",
            "多处行为与章纲或正文已建立的设定冲突，或主要对话只复述设定、角色说话无法区分。",
            "人物大体一致，但一处关键反应缺少动机，或一段较长对话明显只为说明信息。",
            "人物选择与身份动机一致，对话推动关系或冲突，仅一处次要台词略显直白或同质。",
            "行动与反应均符合人物已知处境，不同角色表达可辨，对话每段都有效改变信息、关系或行动。",
        ]),
        ("场景与情绪", "正文场景是否具体可辨，情绪变化是否由本章事件、动作和细节自然支撑？", [
            "主要场景无法辨认，情绪结论与发生的事件脱节，读者无法理解人物为何转变。",
            "多次无铺垫地切换情绪，或关键场面只靠抽象形容词说明紧张、悲伤等感受。",
            "场景基本清楚，但一处关键情绪转折缺少事件或动作支撑，影响感受的可信度。",
            "重要空间和情绪转折均有具体依据，仅一处次要场景或感受描写略笼统。",
            "读者可定位主要动作与空间，情绪随事件和细节逐步变化，无脱离场景的强行煽情。",
        ]),
        ("语言质量", "语言是否清楚自然，且落实章纲及已启用技能中的适用表达要求？没有写明的文风不评分。", [
            "大量句子不通或指代不明，读者无法理解主要情节。",
            "多处表达含混，或大量套话、同义反复挤占叙事。",
            "整体可读，但有一段明显冗长空泛，或一项适用表达要求反复偏离。",
            "语言准确顺畅，适用要求得到落实，仅有局部重复、赘词或语气不协调。",
            "措辞准确、句段清楚，适用要求均落实，无可指认套话、重复或语气漂移。",
        ]),
    ],
}


def list_rules(task_type: str | None = None) -> list[dict]:
    return sorted([rule for rule in repo.list_rules() if task_type is None or rule["task_type"] == task_type],
                  key=lambda item: (item["task_type"], -item["version"]))


def get_rule(rule_id: str) -> dict | None:
    return repo.get_rule(rule_id)


def _matches_text_dimensions(rule: dict, expected: set[str]) -> bool:
    keys = [dimension["key"] for dimension in rule.get("dimensions", [])]
    return rule.get("profile") == TEXT_PROFILE and len(keys) == len(expected) and set(keys) == expected


def is_current_text_rule(rule: dict) -> bool:
    return _matches_text_dimensions(rule, {entry[0] for entry in chapter_rubric.DIMENSIONS})


def save_rule(request: RuleCreate) -> dict:
    with _lock:
        old = list_rules(request.task_type)
        value = request.model_dump(mode="json", exclude_none=True)
        scope = configured_task_type()
        value["profile"] = request.profile or (TEXT_PROFILE if scope == "chapter_text" else "isolated-writing-v1" if scope else PROFILE)
        if configured_task_type():
            value["flow_version"] = 3
        value.update(id=repo.identifier("rule"), version=max((item["version"] for item in old), default=0) + 1, created_at=repo.now())
        repo.insert("self_evolution_rules", value)
        return value


def seed_rules() -> None:
    with _lock:
        for kind, dimensions in _DIMENSIONS.items():
            scope = configured_task_type()
            if (scope and kind != scope) or (not scope and kind == "master_outline"):
                continue
            profile = TEXT_PROFILE if scope == "chapter_text" else "isolated-writing-v1" if scope else PROFILE
            matches = is_current_text_rule if profile == TEXT_PROFILE else lambda rule: rule.get("profile") == profile
            rules = list_rules(kind)
            existing = [rule for rule in rules if matches(rule)]
            if existing:
                if profile == TEXT_PROFILE and not any(
                        all(dimension.get("focus_points") is not None for dimension in rule["dimensions"])
                        for rule in existing):
                    latest = existing[0]
                    save_rule(RuleCreate.model_validate({
                        **{key: latest[key] for key in ("task_type", "profile", "name", "discipline_rules")},
                        "dimensions": [
                            {**dimension, "focus_points": chapter_rubric.FOCUS_POINTS[dimension["key"]]}
                            if dimension.get("focus_points") is None else dimension
                            for dimension in latest["dimensions"]
                        ],
                    }))
                continue
            discipline_rules = DISCIPLINE_RULES
            focus_points = {}
            if profile == TEXT_PROFILE:
                dimensions = chapter_rubric.DIMENSIONS
                discipline_rules = chapter_rubric.DISCIPLINE_RULES
                focus_points = chapter_rubric.FOCUS_POINTS
            payload = {
                "task_type": kind, "profile": profile,
                "name": {"master_outline": "总纲", "volume_outlines": "卷纲", "chapter_outlines": "单章章纲", "chapter_text": "单章正文"}[kind] + "试验规则",
                "discipline_rules": discipline_rules,
                "dimensions": [{"key": key, "question": question, "weight": 1, "focus_points": focus_points.get(key),
                                "anchors": {str(level): text for level, text in enumerate(anchors, 1)}}
                               for key, question, anchors in dimensions],
            }
            if profile == TEXT_PROFILE:
                previous = next((rule for rule in rules if _matches_text_dimensions(rule, _LEGACY_TEXT_KEYS)), None)
                if previous:
                    payload.update(name=previous["name"], discipline_rules=previous["discipline_rules"])
                    payload["dimensions"] = [
                        {**dimension, "focus_points": focus_points[dimension["key"]]}
                        if dimension.get("focus_points") is None else dimension
                        for dimension in previous["dimensions"]
                    ] + [dimension for dimension in payload["dimensions"] if dimension["key"] not in _LEGACY_TEXT_KEYS]
            save_rule(RuleCreate.model_validate(payload))
