"""故事构建目标配比契约（REQ-20260930-002231 FR-006，区块头计数口径）。

storybuilding 运行侧导航（QuotaConvergenceMiddleware）与 benchmark 评分侧共用
的配比解析与核对逻辑——唯一实现放 contracts，两侧不得各自实现，
保证终止语义一致（「同一判定器」原则沿用 DEC-011）。

数据口径（与 golden 评测集 / demand 模板对齐）：
  - demand 核心层字段：``- **目标配比**（主线 / 支线 / 角色线 / 暗线）：主线5 / 支线1 / 角色线1 / 暗线0``
  - 承诺点兜底：``结构约束：篇幅 21-50 章；配比主线5 / 支线1 / 角色线1 / 暗线0。``
  - storyline.md 区块头（唯一手写产物，一线一区块）：
    ``## {线名} · {类型} · {状态}``，类型 ∈ 主线/支线/角色线/暗线；
    类型词允许 **粗体** 包裹（Agent 手写变体先例），状态段允许缺失。

minimal 档 demand 配比留白（无上述字段）→ ``parse_demand_quota`` 返回 None，
调用方走软终止（agent 自判收束），本模块不做任何猜测填充。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# 四种故事线类型的中文键（与 storyline.md 区块头「类型」段、demand 配比字段一致）
LINE_MAIN = "主线"
LINE_SUB = "支线"
LINE_CHAR = "角色线"
LINE_DARK = "暗线"
LINE_TYPES = (LINE_MAIN, LINE_SUB, LINE_CHAR, LINE_DARK)

# 核心层「目标配比」字段：主线5 / 支线1 / 角色线1 / 暗线0
# 允许类型间分隔符为 / 或 ；；数值 0-99；类型顺序固定（demand 模板生成，golden 同构）。
_QUOTA_FIELD_RE = re.compile(
    r"目标配比[^：:]*[：:]\s*"
    r"主线\s*(\d+)\s*[/／、]\s*支线\s*(\d+)\s*[/／、]\s*角色线\s*(\d+)\s*[/／、]\s*暗线\s*(\d+)"
)
# 承诺点「结构约束」行的配比兜底（字段缺失时用；格式同上）
_QUOTA_COMMITMENT_RE = re.compile(
    r"配比\s*主线\s*(\d+)\s*[/／、]\s*支线\s*(\d+)\s*[/／、]\s*角色线\s*(\d+)\s*[/／、]\s*暗线\s*(\d+)"
)

# storyline.md 线区块头：## {线名} · {类型} · {状态}
# 只认二级标题（##）——事件表行、列表行、三级标题（###）一律不匹配；
# 分隔符宽容 · 与 •，类型词允许粗体包裹，状态段允许缺失（行尾收尾）。
_BLOCK_HEADER_RE = re.compile(
    r"(?m)^##\s+[^#\n]*?[·•]\s*\**(" + "|".join(LINE_TYPES) + r")\**(?:\s*[·•]|\s*$)"
)

# 人物档案路径：character/{名}.md
_CHARACTER_FILE_RE = re.compile(r"(?:^|/)character/[^/]+\.md$")


@dataclass(frozen=True)
class QuotaTarget:
    """demand 声明的目标配比（各类型故事线条数上限即目标）。"""

    main: int
    sub: int
    character_line: int
    dark: int
    source: str  # "core"（核心层字段）| "commitment"（承诺点兜底）

    def total(self) -> int:
        return self.main + self.sub + self.character_line + self.dark

    def as_dict(self) -> dict[str, int]:
        return {
            LINE_MAIN: self.main,
            LINE_SUB: self.sub,
            LINE_CHAR: self.character_line,
            LINE_DARK: self.dark,
        }


@dataclass(frozen=True)
class QuotaStatus:
    """配比核对结果。achieved = 各类型实际条数均达到目标（目标 0 恒达标）。"""

    achieved: bool
    target: dict[str, int]
    actual: dict[str, int]
    gaps: dict[str, int]  # 仅未达标类型：目标 - 实际（正数）

    def summary_line(self) -> str:
        """单行中文摘要（注入指令 / 评分记录共用同一表述）。"""
        target_part = " / ".join(f"{k}{v}" for k, v in self.target.items())
        actual_part = " / ".join(f"{k}{v}" for k, v in self.actual.items())
        if self.achieved:
            return f"配比已达标（目标 {target_part}；实际 {actual_part}）"
        gap_part = "、".join(f"{k}还差{v}条" for k, v in self.gaps.items())
        return f"配比未达标（目标 {target_part}；实际 {actual_part}；{gap_part}）"


def parse_demand_quota(demand_md: str) -> QuotaTarget | None:
    """从 demand.md 解析目标配比；无配比字段或格式不可识别返回 None。

    解析顺序：核心层「目标配比」字段优先；缺失时用承诺点「结构约束」行的
    配比兜底。两者都不可用（minimal 档留白、生产表单无配比）返回 None，
    调用方走软终止——本函数不抛异常、不猜测默认值。
    """
    m = _QUOTA_FIELD_RE.search(demand_md) or _QUOTA_COMMITMENT_RE.search(demand_md)
    if m is None:
        return None
    source = "core" if _QUOTA_FIELD_RE.search(demand_md) else "commitment"
    return QuotaTarget(
        main=int(m.group(1)),
        sub=int(m.group(2)),
        character_line=int(m.group(3)),
        dark=int(m.group(4)),
        source=source,
    )


def parse_storyline_blocks(md_content: str) -> dict[str, int] | None:
    """解析 storyline.md 单文件，按线区块头类型词计实际条数。

    无任何可识别区块头时返回 None（类型词不可识别 → 调用方软终止）。
    """
    counts = {t: 0 for t in LINE_TYPES}
    found = False
    for m in _BLOCK_HEADER_RE.finditer(md_content):
        counts[m.group(1)] += 1
        found = True
    return counts if found else None


def count_line_block_headers(md_content: str) -> int:
    """数 storyline.md 中的线区块头总数（不区分类型）。

    供写入护栏（StorylineSingleLineLimitMiddleware）估算区块净增量使用——
    判定器唯一原则：区块头正则不复制第二份。
    """
    return len(_BLOCK_HEADER_RE.findall(md_content))


def count_character_files(keys) -> int:
    """按文件路径计数人物档案（character/*.md）。"""
    return sum(1 for k in keys if _CHARACTER_FILE_RE.search(str(k).strip().lower()))


def count_workspace(workspace_path: Path) -> dict[str, int | None]:
    """物理工作区实况计数（运行时判定入口）。

    Returns:
        ``{"by_type": storyline.md 区块头类型分布或 None, "lines": 线区块总数,
        "characters": 人物档案数}``
    """
    workspace_path = Path(workspace_path)
    storyline_md = workspace_path / "storyline.md"
    by_type: dict[str, int] | None = None
    lines = 0
    if storyline_md.exists():
        by_type = parse_storyline_blocks(storyline_md.read_text(encoding="utf-8"))
        if by_type is not None:
            lines = sum(by_type.values())
    character_dir = workspace_path / "character"
    characters = 0
    if character_dir.is_dir():
        characters = sum(1 for p in character_dir.iterdir() if p.is_file())
    return {"by_type": by_type, "lines": lines, "characters": characters}


def evaluate_quota(target: QuotaTarget, actual_by_type: dict[str, int]) -> QuotaStatus:
    """核对目标配比与实际类型分布。

    achieved 判定：各类型实际 >= 目标（目标 0 恒达标）。actual_by_type 允许
    缺键（按 0 计）——产物刚建好尚未写入全部线时不应误判超标。
    """
    actual = {t: int(actual_by_type.get(t, 0)) for t in LINE_TYPES}
    target_dict = target.as_dict()
    gaps = {
        t: target_dict[t] - actual[t]
        for t in LINE_TYPES
        if target_dict[t] > actual[t]
    }
    return QuotaStatus(
        achieved=not gaps,
        target=target_dict,
        actual=actual,
        gaps=gaps,
    )


__all__ = [
    "LINE_TYPES",
    "QuotaStatus",
    "QuotaTarget",
    "count_character_files",
    "count_line_block_headers",
    "count_workspace",
    "evaluate_quota",
    "parse_demand_quota",
    "parse_storyline_blocks",
]
