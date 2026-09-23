"""故事构建目标配比契约（REQ-20260922-162823 FR-004，DEC-011）。

双架构实验（单 Agent 连续增量版 / 多 Agent 版）与 benchmark 评分侧共用的
配比解析与核对逻辑——唯一实现放 contracts，两实验版本不得各自实现，
保证终止语义一致（DEC-011「同一判定器」）。

数据口径（与 golden 评测集 / demand 模板对齐）：
  - demand 核心层字段：``- **目标配比**（主线 / 支线 / 角色线 / 暗线）：主线5 / 支线1 / 角色线1 / 暗线0``
  - 承诺点兜底：``结构约束：篇幅 21-50 章；配比主线5 / 支线1 / 角色线1 / 暗线0。``
  - storyline.md 一览表行：``| S01 | 名称 | 主线 | 活跃 |``（ID | 名称 | 类型 | 状态）

minimal 档 demand 配比留白（无上述字段）→ ``parse_demand_quota`` 返回 None，
调用方按 DEC-013 走软终止（agent 自判收束），本模块不做任何猜测填充。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# 四种故事线类型的中文键（与 storyline.md 一览表「类型」列、demand 配比字段一致）
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

# storyline.md 一览表数据行：标准四列 | S01 | 名称 | 主线 | 活跃 |；
# 宽表变体 | S01-名字 | 名字 | 主线 | 摘要… |（多 Agent 版实测出现过——
# 第一列 S{XX} 后粘名字，列数更多，类型词可能被 **粗体** 包裹）。
# 两种都按「第 1 或第 3 列命中类型词」计类型。
_INDEX_ROW_RE = re.compile(
    r"^\s*\|\s*S\d{2}(?:[-–][^|]*)?\s*\|[^|]*\|\s*\**(" + "|".join(LINE_TYPES) + r")\**\s*\|",
    re.MULTILINE,
)

# storyline 详情文件（虚拟路径或物理文件名）：storyline/S01-xxx.md / S02-yyy.md
# （[sS] 兼容调用方传入小写化路径；文件名规范为大写 S）
_STORYLINE_FILE_RE = re.compile(r"(?:^|/)[sS]\d{2}[^/]*\.md$")

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
    调用方按 DEC-013 走软终止——本函数不抛异常、不猜测默认值。
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


def parse_storyline_index(storyline_md_content: str) -> dict[str, int] | None:
    """解析 storyline.md 一览表，按类型计实际故事线条数。

    一览表缺失或无 S{XX} 数据行时返回 None（调用方退回文件计数口径）。
    """
    counts = {t: 0 for t in LINE_TYPES}
    found = False
    for m in _INDEX_ROW_RE.finditer(storyline_md_content):
        counts[m.group(1)] += 1
        found = True
    return counts if found else None


def count_storyline_files(keys) -> int:
    """按文件路径计数故事线详情文件（storyline/S{XX}-*.md）。

    一览表不可用时的兜底口径：只给总数，类型分布不可得（None 语义由
    调用方处理）。接受任意可迭代路径/键序列。
    """
    return sum(1 for k in keys if _STORYLINE_FILE_RE.search(str(k).strip().lower()))


def count_character_files(keys) -> int:
    """按文件路径计数人物档案（character/*.md）。"""
    return sum(1 for k in keys if _CHARACTER_FILE_RE.search(str(k).strip().lower()))


def count_workspace(workspace_path: Path) -> dict[str, int | None]:
    """物理工作区实况计数（运行时判定入口）。

    Returns:
        ``{"by_type": 一览表类型分布或 None, "line_files": S 文件总数,
        "characters": 人物档案数}``
    """
    workspace_path = Path(workspace_path)
    storyline_md = workspace_path / "storyline.md"
    by_type = None
    if storyline_md.exists():
        by_type = parse_storyline_index(storyline_md.read_text(encoding="utf-8"))
    storyline_dir = workspace_path / "storyline"
    line_files = 0
    if storyline_dir.is_dir():
        line_files = sum(
            1 for p in storyline_dir.iterdir()
            if p.is_file() and _STORYLINE_FILE_RE.search("/" + p.name)
        )
    character_dir = workspace_path / "character"
    characters = 0
    if character_dir.is_dir():
        characters = sum(1 for p in character_dir.iterdir() if p.is_file())
    return {"by_type": by_type, "line_files": line_files, "characters": characters}


def evaluate_quota(target: QuotaTarget, actual_by_type: dict[str, int]) -> QuotaStatus:
    """核对目标配比与实际类型分布。

    achieved 判定：各类型实际 >= 目标（目标 0 恒达标）。actual_by_type 允许
    缺键（按 0 计）——一览表刚建好尚未列入全部线时不应误判超标。
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
    "count_storyline_files",
    "count_workspace",
    "evaluate_quota",
    "parse_demand_quota",
    "parse_storyline_index",
]
