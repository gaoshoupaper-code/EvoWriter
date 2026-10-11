"""outline 产物结构契约（REQ-20261011-142529 FR-001/005/006，DEC-004/007/017）。

七步管线新产物的「结构语法」判定器唯一实现（REQ-20261010-000638 DEC-010 原则：
结构语法硬拦在 contracts，语义口径归 harness 观测）。覆盖三类：

  - 设计端产物（00-03，storydesign 泳道）：必备章节标题存在性
  - 结构端产物（04-06，storystructure 泳道）：总纲/卷纲/章纲批的必备章节
  - 期待台账：八列结构 + 层级/状态词表 + 已放弃必填备注（DEC-017）

纯静态 markdown 解析，不做语义判断——爽点密度/间隔/悬置时长等指标属
体检报告（观测不拦截，DEC-007）；章纲跨批连续性由体检计算，此处只管批内语法。
"""
from __future__ import annotations

import re
from pathlib import Path

# ── 期待台账词表（DEC-017：outline 台账为基 + PPP 语义）──────────────
LEDGER_COLUMNS = ["ID", "层级", "期待内容", "埋设章", "最近推进章", "计划兑现章", "状态", "备注"]
LEDGER_LEVELS = {"主线", "卷"}
LEDGER_STATUSES = {"活跃", "悬置🔴", "已兑现", "已放弃"}
LEDGER_ID_PATTERN = re.compile(r"^[MV]-\d{2,}$")

# ── 章纲批表列（FR-001 第 6 步产物）────────────────────────────────
BATCH_COLUMNS = ["章", "主要事件", "爽点", "章末钩子", "期待推进"]

# ── 设计端/结构端产物必备章节（模板见两份主文档）──────────────────
_REQUIRED_HEADINGS: dict[str, list[str]] = {
    "00-赛道": ["平台", "品类"],
    "01-设计原则": ["定稿一句话", "四要素对照", "场景推演", "爽点预感"],
    "02-冲突引擎": ["主角卡", "全书大 BOSS 卡", "主线大期待", "反派递进总链条"],
    "03-力量体系": ["境界划分", "金手指规则", "升级-分卷对应表"],
    "04-总纲": ["结局锚点", "分卷表"],
    "05-卷纲": ["本卷定位", "本卷反派", "本卷期待", "大爽点布局表"],
}

# 文件名前缀 → 泳道（写入白名单之外的第二道结构归属判定）
def classify_outline_file(rel_path: str | Path) -> str | None:
    """相对 workspace 路径 → 'design' | 'structure' | None（非 outline 产物）。

    outline/00-03 与 character/core、worldview/core.md 归 design；
    outline/04-06、期待台账、rework 工单、character/support、
    worldview/setting.md、object/ 归 structure。
    """
    p = Path(rel_path).as_posix().lstrip("/")
    if p.startswith("outline/"):
        name = p[len("outline/"):]
        if name.startswith(("00-", "01-", "02-", "03-")):
            return "design"
        if name.startswith(("04-", "05-", "06-")) or name == "期待台账.md":
            return "structure"
        if name.startswith("rework/"):
            return "structure"  # 工单文件由 submit_rework_ticket 工具落盘
        return None
    if p.startswith("character/core/"):
        return "design"
    if p.startswith(("character/support/", "object/", "worldview/setting.md")):
        return "structure"
    if p == "worldview/core.md":
        return "design"
    return None


def _headings(text: str) -> list[str]:
    return [ln.lstrip("#").strip() for ln in text.splitlines() if ln.lstrip().startswith("#")]


def _find_table(text: str, required_cols: list[str]) -> tuple[bool, list[list[str]]]:
    """定位含全部要求列的 markdown 表，返回 (找到, 数据行)。列序必须一致。"""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not (s.startswith("|") and s.endswith("|")):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells[: len(required_cols)] != required_cols:
            continue
        rows: list[list[str]] = []
        for follow in lines[i + 2 :]:  # 跳过分隔行
            f = follow.strip()
            if not (f.startswith("|") and f.endswith("|")):
                break
            rows.append([c.strip() for c in f.strip("|").split("|")])
        return True, rows
    return False, []


def validate_design_artifact(stem: str, text: str) -> list[str]:
    """00-03 设计端产物的必备章节校验。stem 为去扩展名文件名（如 '01-设计原则'）。"""
    if stem == "00-赛道":
        # 00 的平台/品类是列表项（- 平台：番茄），不走标题匹配
        errors = []
        for field in ("平台", "品类"):
            if not re.search(rf"{field}[：:]\s*\S", text):
                errors.append(f"[00-赛道] 缺必填字段「{field}：」")
        return errors
    required = _REQUIRED_HEADINGS.get(stem)
    if required is None:
        return []
    errors: list[str] = []
    have = _headings(text)
    joined = "\n".join(have)
    for h in required:
        if h not in joined:
            errors.append(f"[{stem}] 缺必备章节「{h}」")
    # 03 专属：金手指代价与限制非空（无限制 = 张力清零，属结构语法级红线）
    if stem == "03-力量体系":
        m = re.search(r"代价与限制[：:]\s*(\S.*)", text)
        if not m or m.group(1).strip() in {"无", "待定", "暂无", "—", "-"}:
            errors.append("[03-力量体系] 金手指「代价与限制」必须非空且不得为「无/待定」")
    return errors


def validate_ledger(text: str) -> list[str]:
    """期待台账八列结构 + 词表 + 已放弃备注校验（DEC-017 / FR-006）。"""
    errors: list[str] = []
    ok, rows = _find_table(text, LEDGER_COLUMNS)
    if not ok:
        return ["[期待台账] 缺八列表（ID/层级/期待内容/埋设章/最近推进章/计划兑现章/状态/备注）"]
    for r in rows:
        if not r:
            continue
        rid = r[0] if r else ""
        if not LEDGER_ID_PATTERN.match(rid):
            errors.append(f"[期待台账] ID 不合法: {rid!r}（须 M-01/V-01 形式）")
        if len(r) > 1 and r[1] not in LEDGER_LEVELS:
            errors.append(f"[期待台账] 层级词不合法: {r[1]!r}（须 主线/卷）")
        if len(r) > 6 and r[6] not in LEDGER_STATUSES:
            errors.append(f"[期待台账] 状态词不合法: {r[6]!r}（须 活跃/悬置🔴/已兑现/已放弃）")
        if len(r) > 6 and r[6] == "已放弃":
            note = r[7].strip() if len(r) > 7 else ""
            if not note:
                errors.append(f"[期待台账] {rid} 已放弃但备注为空（放弃须交代位置，DEC-017）")
    return errors


def validate_chapter_batch(text: str) -> list[str]:
    """章纲批结构：前情接点 + 五列表 + 批内章号连续。"""
    errors: list[str] = []
    if not re.search(r"前情接点[：:]", text):
        errors.append("[章纲批] 缺「前情接点：」行")
    ok, rows = _find_table(text, BATCH_COLUMNS)
    if not ok:
        errors.append("[章纲批] 缺五列表（章/主要事件/爽点/章末钩子/期待推进）")
        return errors
    nums: list[int] = []
    for r in rows:
        if not r or not r[0]:
            continue
        if not re.fullmatch(r"\d+", r[0]):
            errors.append(f"[章纲批] 章号非数字: {r[0]!r}")
            continue
        nums.append(int(r[0]))
    for a, b in zip(nums, nums[1:]):
        if b != a + 1:
            errors.append(f"[章纲批] 批内章号不连续: {a} → {b}")
            break
    return errors


def validate_outline_file(rel_path: str | Path, text: str) -> list[str]:
    """按路径分派到对应校验器（guard 统一入口）。非 outline 产物返回空。"""
    p = Path(rel_path).as_posix().lstrip("/")
    name = Path(p).name
    stem = Path(p).stem
    if p == "outline/期待台账.md":
        return validate_ledger(text)
    if p.startswith("outline/06-章纲/") and name.endswith(".md"):
        return validate_chapter_batch(text)
    if p.startswith("outline/") and stem in _REQUIRED_HEADINGS:
        return validate_design_artifact(stem, text)
    return []


def parse_chapter_rows(text: str) -> list[dict[str, str]]:
    """解析章纲批五列表为字典行（体检计算共用；表缺失返回空）。"""
    _, rows = _find_table(text, BATCH_COLUMNS)
    keys = ["章", "主要事件", "爽点", "章末钩子", "期待推进"]
    return [
        {k: (r[i] if i < len(r) else "") for i, k in enumerate(keys)}
        for r in rows
        if r and r[0]
    ]


def parse_ledger_rows(text: str) -> list[dict[str, str]]:
    """解析期待台账八列表为字典行（体检计算共用；表缺失返回空）。"""
    _, rows = _find_table(text, LEDGER_COLUMNS)
    keys = ["ID", "层级", "期待内容", "埋设章", "最近推进章", "计划兑现章", "状态", "备注"]
    return [
        {k: (r[i] if i < len(r) else "") for i, k in enumerate(keys)}
        for r in rows
        if r and r[0]
    ]


__all__ = [
    "BATCH_COLUMNS",
    "LEDGER_COLUMNS",
    "LEDGER_ID_PATTERN",
    "LEDGER_LEVELS",
    "LEDGER_STATUSES",
    "classify_outline_file",
    "parse_chapter_rows",
    "parse_ledger_rows",
    "validate_chapter_batch",
    "validate_design_artifact",
    "validate_ledger",
    "validate_outline_file",
]
