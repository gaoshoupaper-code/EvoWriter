"""Volume payoff contracts and deterministic checks (REQ-20261029-094930).

No model or executor dependencies belong here. Source membership is mechanical;
whether the actual plot fulfils a source remains the independent review's job.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache


CATALOG_VERSION = 2
RHYTHM_LEVELS = ("小", "中", "大", "超大")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class SkillSource:
    skill_id: str
    content: str
    content_hash: str


def policy_hash(sources: list[SkillSource]) -> str:
    return content_hash(json.dumps(
        {"version": CATALOG_VERSION, "sources": [asdict(s) for s in sources]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ))


@dataclass
class PayoffEntry:
    entry_id: str
    name: str
    skill_id: str
    source_quote: str
    behavior_levels: list[str] = field(default_factory=list)


@dataclass
class PayoffCatalog:
    status: str
    policy_hash: str
    sources: list[SkillSource] = field(default_factory=list)
    entries: list[PayoffEntry] = field(default_factory=list)
    allow_combinations: bool = False
    combination_rules: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        raw = asdict(self)
        raw["catalog_hash"] = content_hash(json.dumps(raw, ensure_ascii=False, sort_keys=True))
        return raw

    @classmethod
    def from_dict(cls, raw: dict) -> "PayoffCatalog":
        material = {key: value for key, value in raw.items() if key != "catalog_hash"}
        if raw.get("catalog_hash") != content_hash(json.dumps(material, ensure_ascii=False, sort_keys=True)):
            raise ValueError("清单缓存内容指纹不匹配")
        sources = [SkillSource(**s) for s in raw["sources"]]
        if any(s.content_hash != content_hash(s.content) for s in sources):
            raise ValueError("技能原文指纹不匹配")
        if raw["policy_hash"] != policy_hash(sources):
            raise ValueError("技能快照指纹不匹配")
        entries = [PayoffEntry(**entry) for entry in raw.get("entries", [])]
        source_map = {s.skill_id: s.content for s in sources}
        for entry in entries:
            source = source_map.get(entry.skill_id, "")
            if not entry.source_quote or entry.source_quote not in source or entry.name not in entry.source_quote:
                raise ValueError("条目缺少真实原文出处")
            if any(not level or level not in source for level in entry.behavior_levels):
                raise ValueError("行为档位缺少真实原文出处")
        if raw["status"] not in ("ready", "not_applicable", "ambiguous", "error"):
            raise ValueError("清单状态非法")
        if (raw["status"] == "ready" and not entries) or (raw["status"] == "not_applicable" and entries):
            raise ValueError("清单状态与条目不一致")
        return cls(
            status=raw["status"], policy_hash=raw["policy_hash"], sources=sources,
            entries=entries, allow_combinations=raw.get("allow_combinations", False),
            combination_rules=raw.get("combination_rules", ""), errors=raw.get("errors", []),
        )


@dataclass
class Violation:
    code: str
    message: str
    chapter: int | None = None
    location: str = ""


@dataclass
class PayoffComponent:
    entry_id: str
    behavior_level: str | None = None


@dataclass
class PayoffOccurrence:
    occurrence_id: str
    chapter: int | None
    components: list[PayoffComponent]
    rhythm_level: str
    plot_text: str
    source_text: str
    location: str


@dataclass
class PayoffVerdict:
    candidate_hash: str
    policy_hash: str
    occurrences: list[PayoffOccurrence] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    review_results: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict:
        return {**asdict(self), "passed": self.passed}


_MARKER = re.compile(r"【爽】\s*([^【|\n;；:：。]*?)\s*(?=[【|\n;；:：。]|$)")
_RANGE = re.compile(r"本卷章号范围\s*[:：]\s*(?:第\s*)?(\d+)\s*[-—–~～至]\s*(\d+)\s*章?")
_SEPARATOR = re.compile(r"^:?-{3,}:?$")
_CLAIM_HEAD = re.compile(r"^(?:\*\*)?(?:卷末超大爽点|段末释放|大爽点|中爽点|小爽点)(?:\*\*)?\s*[:：]")


def _clean(text: str) -> str:
    return text.strip().strip("*` ")


def _cells(line: str) -> list[str]:
    return [_clean(cell).replace(r"\|", "|") for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _table_rows(lines: list[str]):
    header: list[str] | None = None
    sections: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            depth = len(heading.group(1))
            while sections and sections[-1][0] >= depth:
                sections.pop()
            sections.append((depth, heading.group(2).strip()))
            header = None
        if not line.strip().startswith("|"):
            header = None
            continue
        cells = _cells(line)
        if i + 1 < len(lines) and lines[i + 1].strip().startswith("|"):
            nxt = _cells(lines[i + 1])
            if nxt and all(_SEPARATOR.fullmatch(c) for c in nxt):
                header = cells
                continue
        if cells and all(_SEPARATOR.fullmatch(c) for c in cells):
            continue
        yield i, " / ".join(title for _, title in sections), header or [], cells


def _column(header: list[str], names: tuple[str, ...]) -> int | None:
    for name in names:
        for i, cell in enumerate(header):
            if cell == name:
                return i
    return None


def _source_match(name: str, aliases: dict[str, list[PayoffEntry]]):
    matches = aliases.get(name, [])
    if matches:
        return matches, None
    annotation = re.fullmatch(r"(.+?)(?:（([^（）]+)）|\(([^()]+)\))", name)
    if annotation:
        original = aliases.get(_clean(annotation.group(1)), [])
        level_note = _clean(annotation.group(2) or annotation.group(3))
        if len(original) == 1 and level_note in original[0].behavior_levels:
            return original, level_note
    return [], None


def _known_segmentations(text: str, separators: str, accepts):
    """Return at most two complete parses; ambiguity must never pick an entry."""
    @lru_cache(maxsize=None)
    def parse(fragment: str):
        paths = []
        ends = [i for i, char in enumerate(fragment) if char in separators] + [len(fragment)]
        for end in ends:
            token = _clean(fragment[:end])
            if not token or not accepts(token):
                continue
            tails = [[]] if end == len(fragment) else parse(fragment[end + 1:].strip())
            for tail in tails:
                paths.append([token, *tail])
                if len(paths) == 2:
                    return paths
        return paths

    return parse(text.strip())


def parse_volume_payoffs(text: str, catalog: PayoffCatalog) -> tuple[list[PayoffOccurrence], list[Violation]]:
    """Parse the generated volume template, checking every explicit payoff claim.

    The source Skill remains free Markdown; the generated rhythm table has a
    fixed contract so a model cannot omit a chapter or hide a second marker.
    """
    occurrences: list[PayoffOccurrence] = []
    violations: list[Violation] = []
    if catalog.status != "ready" or not catalog.entries:
        return [], [Violation("catalog_unavailable", "爽点定义未就绪，不能无约束定稿")]
    aliases: dict[str, list[PayoffEntry]] = {}
    for entry in catalog.entries:
        for alias in {entry.name, entry.entry_id}:
            aliases.setdefault(alias, []).append(entry)
    lines = text.splitlines()
    headings = [re.sub(r"^#{1,6}\s+", "", line).strip() for line in lines if re.match(r"^#{1,6}\s+", line)]
    for section in ("发展阶段", "爽点节奏表", "爽点质检表", "卷末钩子"):
        if not any(section in heading for heading in headings):
            violations.append(Violation("missing_section", f"缺少必备小节：{section}"))
    ranges = list(_RANGE.finditer(text))
    expected: set[int] = set()
    if len(ranges) != 1:
        violations.append(Violation("missing_chapter_range", "完整卷纲须有唯一的「本卷章号范围：起章-末章」"))
    else:
        start, end = (int(value) for value in ranges[0].groups())
        if start < 1 or end < start or end - start > 1000:
            violations.append(Violation("invalid_chapter_range", "本卷章号范围非法"))
        else:
            expected = set(range(start, end + 1))
    row_context: dict[int, tuple[int | None, str, str, str, bool]] = {}
    chapters: set[int] = set()
    rhythm_lines: set[int] = set()
    for index, section, header, cells in _table_rows(lines):
        location = f"第{index + 1}行"
        if "爽点节奏表" in section:
            chapter_idx = _column(header, ("章号", "章", "章节"))
            source_idx = _column(header, ("技能条目／组合成分", "技能条目/组合成分", "技能条目", "组合成分", "爽点来源"))
            grade_idx = _column(header, ("行为档位",))
            rhythm_idx = _column(header, ("节奏级别",))
            plot_idx = _column(header, ("剧情安排", "供给内容", "供给点内容"))
            if None in (chapter_idx, source_idx, grade_idx, rhythm_idx, plot_idx) or len(cells) != len(header):
                violations.append(Violation("invalid_rhythm_table", "节奏表须分别填写章号、剧情、技能来源、行为档位、节奏级别", location=location))
                continue
            chapter_match = re.fullmatch(r"(?:第\s*)?(\d+)\s*章?", cells[chapter_idx])
            if chapter_match is None:
                violations.append(Violation("invalid_chapter", "节奏表必须逐章列出，不能用区间代替", location=location))
                continue
            chapter = int(chapter_match.group(1))
            chapters.add(chapter)
            if expected and chapter not in expected:
                violations.append(Violation("chapter_out_of_range", "章号超出本卷声明范围", chapter, location))
            row_context[index] = (chapter, cells[grade_idx], cells[rhythm_idx], cells[plot_idx], True)
            rhythm_lines.add(index)
            source_cell = cells[source_idx]
            if "【爽】" not in source_cell and not re.search(r"【压[·・]?[小大]?】", source_cell):
                violations.append(Violation("missing_reference", "爽点来源须显式标【爽】并引用已有条目", chapter, location))
        elif cells:
            payoff_columns = [i for i, value in enumerate(header) if "爽点" in value or "段末释放" in value]
            declaration = any("爽点" in value for value in cells[:1]) and not header
            chapter_idx = _column(header, ("章号", "章", "章节"))
            general_quality_check = (
                "爽点质检表" in section and chapter_idx is not None
                and chapter_idx < len(cells)
                and re.fullmatch(r"(?:第\s*)?(\d+)\s*章?", cells[chapter_idx]) is None
            )
            if (declaration or payoff_columns) and not general_quality_check and "【爽】" not in lines[index]:
                violations.append(Violation("missing_reference", "爽点主张须引用已有技能条目", location=location))
    for index, line in enumerate(lines):
        context = row_context.get(index)
        chapter, grade, rhythm, plot, is_rhythm = context or (None, "", "", line, False)
        grade_map: dict[str, str] = {}
        def grade_assignment(item: str) -> bool:
            pair = re.split(r"[:：]", item, maxsplit=1)
            return (len(pair) == 2 and bool(_clean(pair[1]))
                    and not re.search(r"[:：]", pair[1])
                    and len(_source_match(_clean(pair[0]), aliases)[0]) == 1)
        assignments = _known_segmentations(grade, "+＋;；", grade_assignment)
        if len(assignments) > 1:
            violations.append(Violation("ambiguous_behavior_grade", "行为档位映射存在多种解释，请用明确的原条目引用", chapter, f"第{index + 1}行"))
        for item in assignments[0] if len(assignments) == 1 else []:
            pair = re.split(r"[:：]", item, maxsplit=1)
            if _clean(pair[0]) in grade_map:
                violations.append(Violation("ambiguous_behavior_grade", "同一来源的行为档位映射重复", chapter, f"第{index + 1}行"))
            else:
                grade_map[_clean(pair[0])] = _clean(pair[1])
        if _CLAIM_HEAD.match(line.strip()) and "【爽】" not in line:
            violations.append(Violation("missing_reference", "爽点主张须引用已有技能条目", location=f"第{index + 1}行"))
        references = []
        for marker in _MARKER.finditer(line):
            reference = _clean(marker.group(1))
            # A connector between two explicitly marked sources is punctuation,
            # unless the complete spelling is already an original entry name.
            if (reference not in aliases and reference.endswith(("+", "＋"))
                    and re.match(r"\s*【爽】", line[marker.end():])):
                reference = reference[:-1].rstrip()
            if reference in aliases:
                names = [reference]
            elif reference:
                segments = _known_segmentations(reference, "+＋", lambda name: len(_source_match(name, aliases)[0]) == 1)
                if len(segments) > 1:
                    violations.append(Violation("ambiguous_reference", "来源可拆成多组已有条目，请用明确的条目ID", chapter, f"第{index + 1}行"))
                    names = []
                else:
                    names = segments[0] if segments else [reference]
            else:
                names = []
            references.append((marker, reference, names))
        component_count = sum(len(names) for _, _, names in references)
        ordered_levels = [_clean(level) for level in re.split(r"[,，、]", grade)] if not grade_map else []
        if is_rhythm and len(ordered_levels) > 1 and len(ordered_levels) != component_count:
            violations.append(Violation("missing_component_grade", "顺序档位数量须与基础成分数量一致", chapter, f"第{index + 1}行"))
        component_index = 0
        for marker, reference, names in references:
            location = f"第{index + 1}行"
            components: list[PayoffComponent] = []
            if not reference:
                violations.append(Violation("empty_reference", "【爽】后缺少已有技能条目", chapter, location))
            if len(names) > 1 and not catalog.allow_combinations:
                violations.append(Violation("combination_forbidden", "当前技能未允许条目组合", chapter, location))
            seen: set[str] = set()
            for name in names:
                position = component_index
                component_index += 1
                matches, annotation_level = _source_match(name, aliases)
                if len(matches) != 1:
                    violations.append(Violation("unknown_entry", f"条目不在唯一来源清单中：{name or '空条目'}", chapter, location))
                    continue
                entry = matches[0]
                if entry.entry_id in seen:
                    violations.append(Violation("duplicate_component", f"组合重复引用同一基础条目：{entry.name}", chapter, location))
                seen.add(entry.entry_id)
                level = grade_map.get(entry.name, grade_map.get(entry.entry_id, grade_map.get(name)))
                if level is None and len(ordered_levels) > 1 and len(ordered_levels) == component_count:
                    level = ordered_levels[position]
                if level is None and len(names) == 1 and not grade_map and len(ordered_levels) == 1:
                    level = _clean(grade)
                if is_rhythm:
                    if annotation_level and level and annotation_level != level:
                        violations.append(Violation("conflicting_behavior_level", f"{entry.name}的来源档位附注与档位列不一致", chapter, location))
                    if len(names) > 1 and not level:
                        violations.append(Violation("missing_component_grade", f"组合成分缺少独立行为档位：{entry.name}", chapter, location))
                    elif entry.behavior_levels and level not in entry.behavior_levels:
                        violations.append(Violation("invalid_behavior_level", f"{entry.name}的行为档位不符合技能：{level}", chapter, location))
                    elif not entry.behavior_levels and level not in ("", "—", "无"):
                        violations.append(Violation("invalid_behavior_level", f"技能未给{entry.name}定义行为档位", chapter, location))
                components.append(PayoffComponent(entry.entry_id, level or annotation_level or None))
            if is_rhythm and rhythm not in RHYTHM_LEVELS:
                violations.append(Violation("invalid_rhythm_level", f"节奏级别须为小/中/大/超大：{rhythm}", chapter, location))
            occurrences.append(PayoffOccurrence(
                f"L{index + 1}:P{marker.start()}", chapter, components, rhythm,
                plot, line, location,
            ))
    for chapter in sorted(expected - chapters):
        violations.append(Violation("missing_chapter", "本卷章号范围内缺少逐章安排", chapter))
    for chapter in sorted(chapters):
        if not any(o.chapter == chapter and o.components for o in occurrences):
            violations.append(Violation("chapter_without_payoff", "该章没有清单内爽点，须修改剧情", chapter))
    if not rhythm_lines:
        violations.append(Violation("missing_rhythm_rows", "爽点节奏表没有可核对的逐章行"))
    return occurrences, violations


__all__ = [
    "CATALOG_VERSION", "PayoffCatalog", "PayoffComponent", "PayoffEntry",
    "PayoffOccurrence", "PayoffVerdict", "SkillSource", "Violation", "content_hash",
    "parse_volume_payoffs", "policy_hash",
]
