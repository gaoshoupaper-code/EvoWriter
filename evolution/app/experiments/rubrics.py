"""Independent immutable rubric revisions for the three target task types."""
from __future__ import annotations

import threading

from app.benchmark.rubric_v3 import DISCIPLINE_RULES
from app.experiments import repo
from app.experiments.schemas import RuleCreate

_lock = threading.RLock()
_DIMENSIONS = {
    "volume_outlines": [
        ("需求兑现", "完整创作要求是否落实于全部目标卷纲？"),
        ("主线因果", "全书目标、阻力、选择与结果是否形成可信的因果链？"),
        ("章间因果", "卷纲规划的章节推进是否前后相承、转折有依据？"),
        ("分卷节奏", "分卷目标、高潮与回落是否清晰，整书节奏是否合理？"),
    ],
    "chapter_outlines": [
        ("卷纲兑现", "四卷卷纲的事件、目标和约束是否落实到全部目标章纲？"),
        ("状态连续", "章纲中的时间、地点、人物知识与能力状态是否连续？"),
        ("节奏分配", "各章推进量和起伏是否合理，关键事件是否有充足承载？"),
        ("可直接执笔", "目标、冲突、选择和变化是否具体到足以直接写正文？"),
    ],
    "chapter_text": [
        ("章纲兑现", "正文是否完整落实目标章纲的事件、约束和变化？"),
        ("逻辑连续", "动作、时间、地点、认知和因果是否可信且连续？"),
        ("人物与对话", "人物行为与身份动机是否一致，对话是否具体且有作用？"),
        ("场景与情绪", "场景是否清晰可感，情绪变化是否有事件和细节支撑？"),
        ("语言文风", "语言是否通顺具体，是否落实固定文风并避免空泛重复？"),
    ],
}


def list_rules(task_type: str | None = None) -> list[dict]:
    return sorted([rule for rule in repo.list_rules() if task_type is None or rule["task_type"] == task_type],
                  key=lambda item: (item["task_type"], -item["version"]))


def get_rule(rule_id: str) -> dict | None:
    return repo.get_rule(rule_id)


def save_rule(request: RuleCreate) -> dict:
    with _lock:
        old = list_rules(request.task_type)
        value = request.model_dump(mode="json")
        value.update(id=repo.identifier("rule"), version=max((item["version"] for item in old), default=0) + 1, created_at=repo.now())
        repo.insert("self_evolution_rules", value)
        return value


def seed_rules() -> None:
    with _lock:
        for kind, dimensions in _DIMENSIONS.items():
            if list_rules(kind):
                continue
            save_rule(RuleCreate.model_validate({
                "task_type": kind, "name": {"volume_outlines": "整本卷纲", "chapter_outlines": "四卷章纲", "chapter_text": "单章正文"}[kind] + "初始规则",
                "discipline_rules": DISCIPLINE_RULES,
                "dimensions": [{"key": key, "question": question, "weight": 1, "anchors": {
                    "1": "关键目标未兑现，存在可指认的严重缺失或矛盾。",
                    "2": "基本目标部分兑现，存在至少两处影响使用的具体缺陷。",
                    "3": "基本目标兑现，但存在一处实质缺陷或多处局部不足。",
                    "4": "目标完整兑现，无实质缺陷，仅存在一处不影响整体使用的局部不足。",
                    "5": "目标完整兑现，全部相关产物一致，找不出可指认的缺陷。",
                }} for key, question in dimensions],
            }))
