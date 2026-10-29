"""Source-backed extraction and complete independent payoff review."""
from __future__ import annotations

import json

import pytest

from app.domains.writing.payoff_validation import ValidationEngine
from contracts.payoff_constraints import content_hash, parse_volume_payoffs


SKILL = {
    "skill_id": "arbitrary-id", "name": "任意名字", "stage": "volume",
    "content": "# 本书规则\n基础甲：小、中、高。\n基础乙：小、中、高。\n已有条目允许组合。\n示例：甲乙组合。",
}
ROWS = "| 1 | 主角当众完成行动甲 | 【爽】基础甲 | 高 | 小 |\n| 2 | 主角完成行动乙 | 【爽】基础乙 | 小 | 超大 |"
VOLUME = f"""# 卷纲 · 第 1 卷
> 状态：定稿
本卷章号范围：1-2
## 发展阶段
阶段说明。
## 爽点节奏表
| 章号 | 剧情安排 | 技能条目／组合成分 | 行为档位 | 节奏级别 |
|---|---|---|---|---|
{ROWS}
## 爽点质检表
质检说明。
## 卷末钩子
钩子说明。
"""


def extraction():
    return {
        "status": "ready",
        "classifications": [{"skill_id": SKILL["skill_id"], "kind": "definition", "source_quote": "基础甲：小、中、高。"}],
        "entries": [
            {"name": "基础甲", "skill_id": SKILL["skill_id"], "source_quote": "基础甲：小、中、高。", "behavior_levels": ["小", "中", "高"]},
            {"name": "基础乙", "skill_id": SKILL["skill_id"], "source_quote": "基础乙：小、中、高。", "behavior_levels": ["小", "中", "高"]},
        ],
        "combination_rules": "已有条目允许组合。",
    }


class Model:
    def __init__(self, *answers, catalog_reviewer=None):
        self.answers = list(answers)
        self.calls = []
        self.catalog_reviewer = catalog_reviewer

    def __call__(self, messages, purpose):
        self.calls.append((messages, purpose))
        if purpose == "payoff_catalog_review" and json.loads(messages[-1]["content"]).get("mode") == "catalog":
            if self.catalog_reviewer is not None:
                return self.catalog_reviewer(messages)
            payload = json.loads(messages[-1]["content"])
            return {
                "status": "pass", "missing_entries": [], "extra_entries": [], "grading_errors": [],
                "sources": [{"skill_id": s["skill_id"], "status": "pass", "evidence": s["content"]} for s in payload["sources"]],
            }
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer(messages) if callable(answer) else answer


def review_response(messages):
    payload = json.loads(messages[-1]["content"])
    return {
        "catalog_status": "pass", "unlisted_claims": [],
        "catalog_checks": [{"skill_id": s["skill_id"], "status": "pass", "evidence": s["content"]} for s in payload["sources"]],
        "items": [
            {"occurrence_id": o["occurrence_id"], "status": "pass", "evidence": o["plot_text"],
             "components": [{"entry_id": c["entry_id"], "status": "pass", "evidence": o["plot_text"]} for c in o["components"]]}
            for o in payload["occurrences"]
        ],
    }


def test_ready_catalog_uses_arbitrary_skill_names_and_real_source():
    model = Model(extraction())
    catalog = ValidationEngine(model).catalog([SKILL])
    assert catalog.status == "ready"
    assert [e.name for e in catalog.entries] == ["基础甲", "基础乙"]
    assert catalog.sources[0].content_hash == content_hash(SKILL["content"])
    assert model.calls[0][1] == "payoff_catalog"
    assert [purpose for _, purpose in model.calls] == ["payoff_catalog", "payoff_catalog_review"]


def test_no_skills_is_not_applicable_without_model_call():
    model = Model()
    assert ValidationEngine(model).catalog([]).status == "not_applicable"
    assert model.calls == []


@pytest.mark.parametrize("mutation", [
    lambda d: d["entries"][0].update(source_quote="编造出处"),
    lambda d: d["entries"][0].update(name="新基础条目"),
    lambda d: d["entries"][0].update(skill_id="another-user"),
    lambda d: d["entries"][0].update(behavior_levels=["超大"]),
    lambda d: d.update(entries=[]),
    lambda d: d.update(classifications=[]),
])
def test_unverifiable_extraction_never_becomes_ready(mutation):
    answer = extraction()
    mutation(answer)
    assert ValidationEngine(Model(answer)).catalog([SKILL]).status in ("ambiguous", "error")


def test_example_combination_cannot_silently_be_added_as_base():
    answer = extraction()
    answer["entries"].append({"name": "甲乙组合", "skill_id": SKILL["skill_id"], "source_quote": "示例：甲乙组合。", "behavior_levels": []})
    def reject_catalog(messages):
        reply = review_response(messages)
        reply["catalog_status"] = "fail"
        return reply
    engine = ValidationEngine(Model(answer, reject_catalog))
    catalog = engine.catalog([SKILL])
    assert not engine.check(VOLUME, catalog).passed


def test_empty_ready_is_not_unconstrained_not_applicable():
    answer = extraction()
    answer["entries"] = []
    assert ValidationEngine(Model(answer)).catalog([SKILL]).status != "not_applicable"


def test_irrelevant_markdown_requires_independent_not_applicable_review():
    skill = {"skill_id": "style", "content": "每段不超过三行。"}
    answer = {"status": "not_applicable", "entries": [], "classifications": [{"skill_id": "style", "kind": "not_applicable", "source_quote": skill["content"]}]}
    verify = {"status": "pass", "sources": [{"skill_id": "style", "status": "not_applicable", "evidence": skill["content"]}]}
    model = Model(answer, verify)
    assert ValidationEngine(model).catalog([skill]).status == "not_applicable"
    assert model.calls[-1][1] == "payoff_catalog_review"


def test_false_not_applicable_must_not_disable_constraints():
    answer = {"status": "not_applicable", "entries": [], "classifications": [{"skill_id": SKILL["skill_id"], "kind": "not_applicable", "source_quote": SKILL["content"]}]}
    verify = {"status": "fail", "sources": []}
    assert ValidationEngine(Model(answer, verify)).catalog([SKILL]).status == "ambiguous"


@pytest.mark.parametrize("answer", ["not json", TimeoutError("timeout"), {"status": "ready", "entries": []}])
def test_extraction_failure_is_not_an_empty_policy(answer):
    assert ValidationEngine(Model(answer)).catalog([SKILL]).status in ("ambiguous", "error")


def test_independent_review_receives_raw_sources_and_complete_candidate():
    model = Model(extraction(), review_response)
    engine = ValidationEngine(model)
    catalog = engine.catalog([SKILL])
    verdict = engine.check(VOLUME, catalog)
    assert verdict.passed
    assert verdict.candidate_hash == content_hash(VOLUME)
    payload = json.loads(model.calls[-1][0][-1]["content"])
    assert payload["candidate"] == VOLUME
    assert payload["sources"][0]["content"] == SKILL["content"]
    assert len(verdict.review_results) == 2


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(items=[]),
    lambda d: d["items"].pop(),
    lambda d: d["items"].append(d["items"][0]),
    lambda d: d["items"][0].update(status="unknown"),
    lambda d: d["items"][0].update(evidence="伪造剧情证据"),
    lambda d: d["items"][0].update(components=[]),
    lambda d: d["items"][0]["components"][0].update(status="fail"),
    lambda d: d.update(catalog_checks=[]),
    lambda d: d.update(catalog_status="unknown"),
    lambda d: d.update(unlisted_claims=["另造的爽点"]),
])
def test_missing_unknown_duplicate_or_fabricated_reviews_fail(mutation):
    def answer(messages):
        result = review_response(messages)
        mutation(result)
        return result
    engine = ValidationEngine(Model(extraction(), answer))
    assert not engine.check(VOLUME, engine.catalog([SKILL])).passed


def test_legal_label_with_wrong_plot_fails_semantic_review():
    def reject(messages):
        answer = review_response(messages)
        answer["items"][0].update(status="fail", reason="只有获得能力，没有对应行动")
        return answer
    engine = ValidationEngine(Model(extraction(), reject))
    assert not engine.check(VOLUME.replace("主角当众完成行动甲", "主角获得能力"), engine.catalog([SKILL])).passed


def test_structural_violation_does_not_spend_semantic_call():
    model = Model(extraction())
    engine = ValidationEngine(model)
    verdict = engine.check(VOLUME.replace("【爽】基础甲", "【爽】金手指到手"), engine.catalog([SKILL]))
    assert not verdict.passed
    assert [purpose for _, purpose in model.calls] == ["payoff_catalog", "payoff_catalog_review"]


def test_review_timeout_is_fail_closed():
    engine = ValidationEngine(Model(extraction(), TimeoutError("timeout")))
    assert not engine.check(VOLUME, engine.catalog([SKILL])).passed


def test_repair_makes_exactly_one_call_and_never_declares_pass():
    model = Model(extraction(), {"candidate": VOLUME})
    engine = ValidationEngine(model)
    catalog = engine.catalog([SKILL])
    _, violations = parse_volume_payoffs(VOLUME.replace("基础甲", "非法甲"), catalog)
    assert engine.repair("旧候选", violations, catalog) == VOLUME
    assert [purpose for _, purpose in model.calls] == ["payoff_catalog", "payoff_catalog_review", "payoff_repair"]


def test_catalog_cache_is_bound_to_exact_source_content(tmp_path):
    cache = tmp_path / "catalog.json"
    first = ValidationEngine(Model(extraction()), cache_path=cache).catalog([SKILL])
    assert ValidationEngine(Model(), cache_path=cache).catalog([SKILL]).policy_hash == first.policy_hash
    changed = {**SKILL, "content": SKILL["content"] + "\n新增约束。"}
    model = Model(extraction())
    second = ValidationEngine(model, cache_path=cache).catalog([changed])
    assert len(model.calls) == 2
    assert second.policy_hash != first.policy_hash


def test_cached_source_or_entry_tampering_is_not_trusted(tmp_path):
    cache = tmp_path / "catalog.json"
    ValidationEngine(Model(extraction()), cache_path=cache).catalog([SKILL])
    raw = json.loads(cache.read_text(encoding="utf-8"))
    raw["entries"][0]["source_quote"] = "编造出处"
    cache.write_text(json.dumps(raw), encoding="utf-8")
    model = Model(extraction())
    assert ValidationEngine(model, cache_path=cache).catalog([SKILL]).status == "ready"
    assert len(model.calls) == 2


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(status="not_applicable", entries=[]),
    lambda d: d["entries"][0].update(behavior_levels=["超大"]),
])
def test_cache_cannot_turn_a_ready_definition_into_unconstrained_or_regraded(tmp_path, mutation):
    cache = tmp_path / "catalog.json"
    ValidationEngine(Model(extraction()), cache_path=cache).catalog([SKILL])
    raw = json.loads(cache.read_text(encoding="utf-8"))
    mutation(raw)
    cache.write_text(json.dumps(raw), encoding="utf-8")
    model = Model(extraction())
    catalog = ValidationEngine(model, cache_path=cache).catalog([SKILL])
    assert catalog.status == "ready"
    assert catalog.entries[0].behavior_levels == ["小", "中", "高"]
    assert len(model.calls) == 2


def test_incomplete_catalog_is_reextracted_before_it_can_become_cached_authority(tmp_path):
    incomplete = extraction()
    incomplete["entries"].pop()

    def inspect(messages):
        payload = json.loads(messages[-1]["content"])
        omitted = "基础乙" not in {e["name"] for e in payload["catalog"]["entries"]}
        return {
            "status": "fail" if omitted else "pass",
            "missing_entries": ["基础乙"] if omitted else [], "extra_entries": [], "grading_errors": [],
            "sources": [{"skill_id": SKILL["skill_id"], "status": "fail" if omitted else "pass", "evidence": SKILL["content"]}],
        }

    model = Model(incomplete, extraction(), catalog_reviewer=inspect)
    cache = tmp_path / "catalog.json"
    catalog = ValidationEngine(model, cache_path=cache).catalog([SKILL])
    assert catalog.status == "ready"
    assert {e.name for e in catalog.entries} == {"基础甲", "基础乙"}
    assert [purpose for _, purpose in model.calls].count("payoff_catalog") == 2
    assert {e["name"] for e in json.loads(cache.read_text())["entries"]} == {"基础甲", "基础乙"}


def test_repeated_catalog_incompleteness_is_ambiguous_and_not_cached(tmp_path):
    def reject(messages):
        return {"status": "fail", "sources": [], "missing_entries": ["遗漏原条目"], "extra_entries": [], "grading_errors": []}

    model = Model(extraction(), extraction(), catalog_reviewer=reject)
    cache = tmp_path / "catalog.json"
    catalog = ValidationEngine(model, cache_path=cache).catalog([SKILL])
    assert catalog.status == "ambiguous"
    assert not cache.exists()
    assert len(model.calls) == 4


def test_catalog_review_requests_a_contiguous_quote_separate_from_reason():
    model = Model(extraction())
    assert ValidationEngine(model).catalog([SKILL]).status == "ready"
    prompt = next(messages[0]["content"] for messages, purpose in model.calls if purpose == "payoff_catalog_review")
    assert "连续" in prompt
    assert "JSON解码" in prompt
    assert "reason" in prompt
    assert "拼接" in prompt and "省略" in prompt


def test_content_review_requires_contiguous_source_plot_and_component_evidence():
    model = Model(extraction(), review_response)
    engine = ValidationEngine(model)
    assert engine.check(VOLUME, engine.catalog([SKILL])).passed
    prompt = next(messages[0]["content"] for messages, purpose in model.calls if purpose == "payoff_review")
    assert "catalog_checks.evidence" in prompt
    assert "items[].evidence" in prompt
    assert "components[].evidence" in prompt
    assert "连续" in prompt and "JSON解码" in prompt


def test_real_probe_explanatory_catalog_evidence_still_cannot_pass(tmp_path):
    # The real model quoted two separate rows, then appended its own explanation.
    def explanatory_evidence(messages):
        return {
            "status": "pass", "missing_entries": [], "extra_entries": [], "grading_errors": [],
            "sources": [{"skill_id": SKILL["skill_id"], "status": "pass",
                         "evidence": "原表共两行基础条目：『基础甲：小、中、高。』『基础乙：小、中、高。』。清单与原文逐一对应，无遗漏。"}],
        }

    model = Model(extraction(), extraction(), catalog_reviewer=explanatory_evidence)
    cache = tmp_path / "catalog.json"
    assert ValidationEngine(model, cache_path=cache).catalog([SKILL]).status == "ambiguous"
    assert not cache.exists()
    assert [purpose for _, purpose in model.calls].count("payoff_catalog") == 2


@pytest.mark.parametrize("target", ["catalog", "item", "component"])
def test_explanation_wrapped_around_valid_evidence_is_rejected(target):
    def answer(messages):
        response = review_response(messages)
        item = response["catalog_checks"][0] if target == "catalog" else response["items"][0]
        if target == "component":
            item = item["components"][0]
        item["evidence"] = "原文对应「" + item["evidence"] + "」，因此通过。"
        return response

    engine = ValidationEngine(Model(extraction(), answer))
    assert not engine.check(VOLUME, engine.catalog([SKILL])).passed


def test_repair_receives_the_exact_volume_table_and_component_format():
    model = Model(extraction(), {"candidate": VOLUME})
    engine = ValidationEngine(model)
    policy = engine.catalog([SKILL])
    assert engine.repair("旧候选", [], policy) == VOLUME
    prompt = next(messages[0]["content"] for messages, purpose in model.calls if purpose == "payoff_repair")
    assert "章号 | 剧情安排 | 技能条目／组合成分 | 行为档位 | 节奏级别" in prompt
    assert "【爽】原条目甲+原条目乙" in prompt
    assert "原条目甲:小+原条目乙:高" in prompt
    assert "一次【爽】" in prompt


def test_separate_combination_quotes_keep_source_and_original_newlines():
    skill = {**SKILL, "content": SKILL["content"] + "\n\n组合须保持原行为。\n叠加规则另行核对。"}
    answer = extraction()
    answer["combination_rules"] = [
        {"skill_id": SKILL["skill_id"], "source_quote": "已有条目允许组合。"},
        {"skill_id": SKILL["skill_id"], "source_quote": "组合须保持原行为。\n叠加规则另行核对。"},
    ]
    catalog = ValidationEngine(Model(answer)).catalog([skill])
    assert catalog.status == "ready"
    assert catalog.combination_rules == answer["combination_rules"]


def test_legacy_contiguous_rule_string_is_attributed_to_its_source():
    catalog = ValidationEngine(Model(extraction())).catalog([SKILL])
    assert catalog.combination_rules == [{"skill_id": SKILL["skill_id"], "source_quote": "已有条目允许组合。"}]


@pytest.mark.parametrize("rules", [
    [{"skill_id": "wrong-source", "source_quote": "已有条目允许组合。"}],
    [{"skill_id": SKILL["skill_id"], "source_quote": "组合自然会变强。"}],
    [{"skill_id": SKILL["skill_id"], "source_quote": "已有条目允许组合。", "reason": "说明"},
     {"skill_id": SKILL["skill_id"], "source_quote": "基础甲：小、中、高。基础乙：小、中、高。"}],
    "已有条目允许组合。基础甲：小、中、高。",
])
def test_rule_quote_lists_do_not_accept_wrong_sources_or_joined_paraphrases(rules):
    answer = extraction()
    answer["combination_rules"] = rules
    assert ValidationEngine(Model(answer)).catalog([SKILL]).status in ("ambiguous", "error")
