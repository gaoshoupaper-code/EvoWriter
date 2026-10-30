"""Closed payoff sources, complete chapter coverage and independent grades."""
from dataclasses import asdict

import pytest

from contracts.payoff_constraints import (
    PayoffCatalog,
    PayoffEntry,
    PayoffVerdict,
    SkillSource,
    content_hash,
    parse_volume_payoffs,
    policy_hash,
)


@pytest.fixture
def policy():
    source = "基础甲：小、中、高。基础乙：小、中、高。允许已有条目组合。"
    return PayoffCatalog(
        status="ready", policy_hash=content_hash(source),
        sources=[SkillSource("skill-a", source, content_hash(source))],
        entries=[
            PayoffEntry("a", "基础甲", "skill-a", "基础甲：小、中、高", ["小", "中", "高"]),
            PayoffEntry("b", "基础乙", "skill-a", "基础乙：小、中、高", ["小", "中", "高"]),
        ], allow_combinations=True,
    )


def volume(rows, *, span="1-2", extra="", unified=False):
    header = "章号 | 剧情安排 | 技能条目／组合成分 | 爽点级别" if unified else "章号 | 剧情安排 | 技能条目／组合成分 | 行为档位 | 节奏级别"
    separator = "|---|---|---|---|" if unified else "|---|---|---|---|---|"
    return f"""# 卷纲 · 第 1 卷
> 状态：定稿
本卷章号范围：{span}
## 发展阶段
阶段说明。
## 爽点节奏表
| {header} |
{separator}
{rows}
## 爽点质检表
质检说明。
## 卷末钩子
钩子说明。
{extra}
"""


GOOD_ROWS = "| 1 | 实际行动甲 | 【爽】基础甲 | 高 | 小 |\n| 2 | 实际行动乙 | 【爽】基础乙 | 小 | 超大 |"


def codes(text, policy):
    return {v.code for v in parse_volume_payoffs(text, policy)[1]}


def test_valid_volume_and_serializable_contract(policy):
    occurrences, violations = parse_volume_payoffs(volume(GOOD_ROWS), policy)
    assert violations == []
    assert [o.chapter for o in occurrences] == [1, 2]
    assert occurrences[0].components[0].behavior_level == "高"
    assert occurrences[0].rhythm_level == "小"
    assert asdict(policy)["sources"][0]["skill_id"] == "skill-a"


@pytest.mark.parametrize("level", ["小", "中", "大", "超大"])
def test_unified_level_does_not_require_independent_behavior_grade(policy, level):
    text = volume(f"| 1 | 实际行动甲 | 【爽】基础甲 | {level} |", span="1-1", unified=True)
    occurrences, violations = parse_volume_payoffs(text, policy)
    assert violations == []
    assert occurrences[0].rhythm_level == level
    assert occurrences[0].components[0].behavior_level is None


@pytest.mark.parametrize("extra", [
    "来源：【爽】基础甲+基础乙；爽点级别：超大。",
    "（第60章为【爽】基础甲+基础乙；每个成分均实际发生）",
])
def test_narrative_combination_ends_before_explanation(policy, extra):
    text = volume("| 1 | 行动甲与行动乙 | 【爽】基础甲+基础乙 | 大 |",
                  span="1-1", unified=True, extra=extra)
    occurrences, violations = parse_volume_payoffs(text, policy)
    assert violations == []
    assert [c.entry_id for c in occurrences[-1].components] == ["a", "b"]


def test_unified_combination_keeps_all_components_without_grade_mapping(policy):
    text = volume("| 1 | 行动甲与行动乙 | 【爽】基础甲+基础乙 | 大 |", span="1-1", unified=True)
    occurrences, violations = parse_volume_payoffs(text, policy)
    assert violations == []
    assert [c.entry_id for c in occurrences[0].components] == ["a", "b"]
    assert all(c.behavior_level is None for c in occurrences[0].components)


@pytest.mark.parametrize("row, expected", [
    ("| 1 | 行动甲 | 【爽】基础甲 | 高 |", "invalid_rhythm_level"),
    ("| 1 | 行动甲 | 【爽】基础甲 | |", "invalid_rhythm_level"),
    ("| 1 | 行动甲 | 【爽】基础甲 | 基础甲:高 |", "invalid_rhythm_level"),
    ("| 1 | 行动甲 | 【爽】基础甲；【爽】新条目 | 小 |", "unknown_entry"),
    ("| 1 | 行动甲与行动乙 | 【爽】基础甲+新条目 | 大 |", "unknown_entry"),
    ("| 1 | 行动甲 | 【爽】基础甲+基础甲 | 大 |", "duplicate_component"),
    ("| 1 | 只有压抑 | 【压·小】 | 小 |", "chapter_without_payoff"),
    ("| 1-2 | 行动甲 | 【爽】基础甲 | 小 |", "invalid_chapter"),
    ("| 1 | 行动甲 | 【爽】基础甲 | 小 | 额外单元格 |", "invalid_rhythm_table"),
])
def test_unified_table_still_rejects_invalid_levels_sources_and_rows(policy, row, expected):
    assert expected in codes(volume(row, span="1-1", unified=True), policy)


def test_unified_table_still_requires_every_declared_chapter(policy):
    text = volume("| 1 | 行动甲 | 【爽】基础甲 | 小 |", unified=True)
    assert "missing_chapter" in codes(text, policy)


def test_unified_table_does_not_bypass_combination_permission(policy):
    policy.allow_combinations = False
    text = volume("| 1 | 行动甲与行动乙 | 【爽】基础甲+基础乙 | 大 |", span="1-1", unified=True)
    assert "combination_forbidden" in codes(text, policy)


def test_legacy_rhythm_level_does_not_require_behavior_column(policy):
    text = volume("| 1 | 行动甲 | 【爽】基础甲 | 小 |", span="1-1", unified=True)
    assert parse_volume_payoffs(text.replace("爽点级别", "节奏级别"), policy)[1] == []


def test_unknown_label_rejected_even_alongside_valid_payoff(policy):
    text = volume(GOOD_ROWS.replace("【爽】基础甲", "【爽】基础甲；【爽】金手指到手"))
    assert "unknown_entry" in codes(text, policy)


def test_combination_resolves_each_original_component(policy):
    text = volume("| 1 | 行动甲与行动乙 | 【爽】基础甲+基础乙 | 基础甲:小+基础乙:高 | 大 |", span="1-1")
    occurrences, violations = parse_volume_payoffs(text, policy)
    assert violations == []
    assert [c.entry_id for c in occurrences[0].components] == ["a", "b"]
    assert [c.behavior_level for c in occurrences[0].components] == ["小", "高"]


def test_entry_ids_are_accepted_without_renaming_sources(policy):
    text = volume(GOOD_ROWS.replace("基础甲", "a").replace("基础乙", "b"))
    assert parse_volume_payoffs(text, policy)[1] == []


@pytest.mark.parametrize("replacement, expected", [
    ("基础甲+新条目", "unknown_entry"),
    ("基础甲+基础甲", "duplicate_component"),
])
def test_invalid_combination_components(policy, replacement, expected):
    assert expected in codes(volume(GOOD_ROWS.replace("基础甲", replacement, 1)), policy)


@pytest.mark.parametrize("grade", ["高", "中＋半档", "", "小、高,中"])
def test_legacy_combination_grades_are_optional_hints(policy, grade):
    text = volume(f"| 1 | 行动甲与行动乙 | 【爽】基础甲+基础乙 | {grade} | 大 |", span="1-1")
    occurrences, violations = parse_volume_payoffs(text, policy)
    assert violations == []
    assert [c.entry_id for c in occurrences[0].components] == ["a", "b"]


def test_missing_middle_or_tail_chapter_cannot_pass(policy):
    assert "missing_chapter" in codes(volume(GOOD_ROWS, span="1-3"), policy)
    assert "missing_chapter" in codes(volume(GOOD_ROWS.replace("| 2 |", "| 3 |"), span="1-3"), policy)


def test_explicit_chapter_boundary_is_required(policy):
    assert "missing_chapter_range" in codes(volume(GOOD_ROWS).replace("本卷章号范围：1-2", ""), policy)


def test_pressure_only_chapter_does_not_count_as_payoff(policy):
    text = volume(GOOD_ROWS.replace("【爽】基础乙", "【压·小】"))
    assert "chapter_without_payoff" in codes(text, policy)


@pytest.mark.parametrize("old,new,expected", [
    ("| 高 | 小 |", "| 高 | 高 |", "invalid_rhythm_level"),
])
def test_behavior_and_rhythm_levels_do_not_substitute(policy, old, new, expected):
    assert expected in codes(volume(GOOD_ROWS.replace(old, new)), policy)


def test_markers_outside_rhythm_table_are_also_enumerated(policy):
    text = volume(GOOD_ROWS, extra="卷末安排：【爽】金手指到手。")
    assert "unknown_entry" in codes(text, policy)


def test_marker_hidden_inside_plot_cell_does_not_escape_scan(policy):
    text = volume(GOOD_ROWS.replace("实际行动甲", "实际行动甲；【爽】获宝"))
    assert "unknown_entry" in codes(text, policy)


def test_declared_climax_needs_source_even_without_marker(policy):
    text = volume(GOOD_ROWS, extra="| 卷末超大爽点 | 金手指到手 |")
    assert "missing_reference" in codes(text, policy)


def test_empty_or_malformed_source_cannot_count(policy):
    assert "empty_reference" in codes(volume(GOOD_ROWS.replace("【爽】基础甲", "【爽】")), policy)


def test_no_full_volume_without_required_sections(policy):
    assert "missing_section" in codes(volume(GOOD_ROWS).replace("## 卷末钩子", "## 其他"), policy)


def test_verdict_pass_is_computed_not_provided(policy):
    occurrences, violations = parse_volume_payoffs(volume(GOOD_ROWS), policy)
    verdict = PayoffVerdict(content_hash(volume(GOOD_ROWS)), policy.policy_hash, occurrences, violations)
    assert verdict.passed
    assert "passed" not in asdict(verdict)


def test_nonready_policy_is_not_a_valid_definition(policy):
    policy.status = "ambiguous"
    assert "catalog_unavailable" in codes(volume(GOOD_ROWS), policy)


def test_rhythm_tables_under_stage_subheadings_are_still_checked(policy):
    text = volume(GOOD_ROWS).replace("## 爽点节奏表\n", "## 爽点节奏表\n### 阶段一\n")
    assert parse_volume_payoffs(text, policy)[1] == []


def test_literal_plus_inside_an_original_entry_name_is_not_a_new_combination(policy):
    policy.entries[0].name = "基础甲+原名"
    text = volume(GOOD_ROWS.replace("【爽】基础甲", "【爽】基础甲+原名"))
    assert parse_volume_payoffs(text, policy)[1] == []


def test_independent_payoffs_in_one_chapter_have_separate_grades(policy):
    text = volume("| 1 | 先行动甲，再行动乙 | 【爽】基础甲；【爽】基础乙 | 基础甲:小+基础乙:中 | 大 |", span="1-1")
    occurrences, violations = parse_volume_payoffs(text, policy)
    assert violations == []
    assert [o.components[0].behavior_level for o in occurrences] == ["小", "中"]


def test_real_probe_general_quality_checks_are_not_extra_payoff_claims(policy):
    # The real model added these two non-chapter checks below its payoff rows.
    quality = """| 章号 | 声称爽点 | 条目存在性原文证据（完整引用） | 档位核对 | 剧情落地核对 | 结论 |
|---|---|---|---|---|---|
| 1 | 【爽】基础甲 | 原技能已有基础甲 | 高 | 实际行动甲 | 通过 |
| 通用 | 行为档位与节奏级别分离 | 行为程度与节奏级别分开 | 分列两栏，未混用 | 通过 |
| 通用 | 未新增条目、仅用已有基础条目 | 仅限已有基础条目 | 无组合、无新增 | 通过 |"""
    text = volume(GOOD_ROWS).replace("质检说明。", quality)
    assert parse_volume_payoffs(text, policy)[1] == []


def test_general_quality_row_cannot_hide_an_explicit_illegal_marker(policy):
    quality = """| 章号 | 声称爽点 | 核对 |
|---|---|---|
| 通用 | 【爽】金手指到手 | 通过 |"""
    text = volume(GOOD_ROWS).replace("质检说明。", quality)
    assert "unknown_entry" in codes(text, policy)


def test_chapter_quality_row_still_requires_payoff_source(policy):
    quality = """| 章号 | 声称爽点 | 核对 |
|---|---|---|
| 1 | 金手指到手 | 通过 |"""
    text = volume(GOOD_ROWS).replace("质检说明。", quality)
    assert "missing_reference" in codes(text, policy)


@pytest.mark.parametrize("source", ["【爽】基础甲＋【爽】基础乙", "【爽】基础甲（小）＋【爽】基础乙（高）"])
def test_real_repair_repeated_markers_support_ordered_component_grades(policy, source):
    text = volume(f"| 1 | 先行动甲，再行动乙 | {source} | 小、高 | 超大 |", span="1-1")
    occurrences, violations = parse_volume_payoffs(text, policy)
    assert violations == []
    assert [o.components[0].entry_id for o in occurrences] == ["a", "b"]
    assert [o.components[0].behavior_level for o in occurrences] == ["小", "高"]


def test_ordered_grade_differences_do_not_block_real_sources(policy):
    policy.entries[0].behavior_levels = ["小"]
    policy.entries[1].behavior_levels = ["高"]
    text = volume("| 1 | 先行动甲，再行动乙 | 【爽】基础甲＋【爽】基础乙 | 高、小 | 超大 |", span="1-1")
    assert parse_volume_payoffs(text, policy)[1] == []


@pytest.mark.parametrize("source", [
    "【爽】基础甲＋", "【爽】基础甲＋；【爽】基础乙", "【爽】＋【爽】基础乙",
    "【爽】新条目（小）＋【爽】基础乙",
])
def test_format_tolerance_does_not_accept_empty_or_forged_sources(policy, source):
    text = volume(f"| 1 | 先行动甲，再行动乙 | {source} | 小、高 | 超大 |", span="1-1")
    assert {"unknown_entry", "empty_reference"} & codes(text, policy)


def test_source_grade_annotation_difference_is_only_a_hint(policy):
    text = volume("| 1 | 行动甲 | 【爽】基础甲（小） | 高 | 大 |", span="1-1")
    assert parse_volume_payoffs(text, policy)[1] == []


def test_original_parenthesized_name_takes_priority_over_grade_annotation(policy):
    policy.entries[0].name = "基础甲（小）"
    policy.entries[0].behavior_levels = ["高"]
    text = volume(GOOD_ROWS.replace("【爽】基础甲", "【爽】基础甲（小）"))
    occurrences, violations = parse_volume_payoffs(text, policy)
    assert violations == []
    assert occurrences[0].components[0].behavior_level == "高"


def test_literal_trailing_plus_is_not_removed_when_it_is_the_original_name(policy):
    policy.entries[0].name = "基础甲＋"
    text = volume("| 1 | 先行动甲，再行动乙 | 【爽】基础甲＋【爽】基础乙 | 高、小 | 大 |", span="1-1")
    occurrences, violations = parse_volume_payoffs(text, policy)
    assert violations == []
    assert [o.components[0].entry_id for o in occurrences] == ["a", "b"]


@pytest.mark.parametrize("original", ["基础甲+原名", "基础甲（原名+扩展）"])
def test_original_plus_name_in_combination_and_grade_mapping_stays_one_entry(policy, original):
    policy.entries[0].name = original
    text = volume(f"| 1 | 先行动甲，再行动乙 | 【爽】{original}+基础乙 | {original}:小+基础乙:高 | 大 |", span="1-1")
    occurrences, violations = parse_volume_payoffs(text, policy)
    assert violations == []
    assert [c.entry_id for c in occurrences[0].components] == ["a", "b"]
    assert [c.behavior_level for c in occurrences[0].components] == ["小", "高"]


def test_truly_ambiguous_alias_segmentation_is_rejected(policy):
    policy.entries = [
        PayoffEntry("ab", "甲+乙", "skill-a", "甲+乙", ["小"]),
        PayoffEntry("c", "丙", "skill-a", "丙", ["高"]),
        PayoffEntry("a", "甲", "skill-a", "甲", ["小"]),
        PayoffEntry("bc", "乙+丙", "skill-a", "乙+丙", ["高"]),
    ]
    text = volume("| 1 | 发生对应行动 | 【爽】甲+乙+丙 | 小、高 | 大 |", span="1-1")
    assert "ambiguous_reference" in codes(text, policy)


def test_unmatched_plus_parts_cannot_create_new_components(policy):
    policy.entries[0].name = "基础甲+原名"
    text = volume("| 1 | 行动 | 【爽】基础甲+新词+基础乙 | 小、高 | 大 |", span="1-1")
    assert "unknown_entry" in codes(text, policy)


@pytest.mark.parametrize("grade", ["超大", "中＋半档", "", "—"])
def test_single_source_grade_does_not_gate_volume(policy, grade):
    text = volume(f"| 1 | 行动甲 | 【爽】基础甲 | {grade} | 大 |", span="1-1")
    assert parse_volume_payoffs(text, policy)[1] == []


def test_annotated_source_does_not_depend_on_extracted_grade_hints(policy):
    policy.entries[0].behavior_levels = []
    text = volume("| 1 | 行动甲 | 【爽】基础甲（中＋半档） | 大 |", span="1-1", unified=True)
    occurrences, violations = parse_volume_payoffs(text, policy)
    assert violations == []
    assert occurrences[0].components[0].behavior_level == "中＋半档"


@pytest.mark.parametrize("levels, expected", [
    ([" 小 ", "小", "中＋半档", "超大", None, {}], ["小"]),
    (None, []),
    ("高", ["高"]),
    ({"level": "小"}, []),
])
def test_cached_grade_hints_are_normalized_without_invalidating_sources(policy, levels, expected):
    policy.policy_hash = policy_hash(policy.sources)
    policy.entries[0].behavior_levels = levels
    restored = PayoffCatalog.from_dict(policy.to_dict())
    assert restored.entries[0].behavior_levels == expected
    assert restored.entries[0].name == "基础甲"


@pytest.mark.parametrize("rules", [
    [{"skill_id": "unknown-source", "source_quote": "允许已有条目组合。"}],
    [{"skill_id": "skill-a", "source_quote": "原文未写的规则。"}],
    [{"skill_id": "skill-a", "source_quote": ""}],
])
def test_cached_combination_rules_each_need_their_exact_source(policy, rules):
    policy.policy_hash = policy_hash(policy.sources)
    policy.combination_rules = rules
    with pytest.raises(ValueError, match="组合规则"):
        PayoffCatalog.from_dict(policy.to_dict())
