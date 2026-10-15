"""三步产物结构契约（REQ-20261015-161200 FR-001/004/005，DEC-001/008/010）。

三步管线产物的「结构语法」判定器唯一实现（结构硬拦在 contracts，语义
口径归 Prompt 与人工抽查）。覆盖：

  - 第一步产物 outline/世界观.md：必备小节（核心冲突/力量体系/金手指/
    大法则）+ 金手指代价限制红线
  - 第二步产物 outline/总纲.md、outline/卷纲-第N卷.md：必备小节
  - 第三步产物 outline/章纲-卷N批M.md：前情接点 + 四列表 + 章号连续；
    outline/期待台账.md：六列结构 + 词表 + 已放弃必带原因

纯静态 markdown 解析，不做语义判断。旧七步产物文件（00-06 前缀）不属
三步结构，返回空（legacy workspace 只读）。
"""
from __future__ import annotations

import re
from pathlib import Path

# ── 期待台账词表（DEC-010：六列）────────────────────────────────────
LEDGER_COLUMNS = ["编号", "层级", "钩子", "埋设章", "兑现章", "状态"]
LEDGER_LEVELS = {"主线", "卷"}
LEDGER_STATUSES = {"活跃", "已兑现"}
LEDGER_ID_PATTERN = re.compile(r"^[MV]-\d{2,}$")
# 「已放弃」必须在状态列带原因（DEC-010：放弃原因并入状态列）
LEDGER_ABANDONED_PATTERN = re.compile(r"^已放弃[：:]\s*\S")

# ── 章纲批表列（FR-005 章纲批产物）──────────────────────────────────
BATCH_COLUMNS = ["章", "剧情走向", "章末钩子", "期待动作"]

# ── 三步产物文件名形态（executor 路由与桌面进度推导共用）────────────
WORLDVIEW_FILENAME = "世界观.md"
MASTER_FILENAME = "总纲.md"
LEDGER_FILENAME = "期待台账.md"
VOLUME_FILE_PATTERN = re.compile(r"^卷纲-第(\d+)卷\.md$")
BATCH_FILE_PATTERN = re.compile(r"^章纲-卷(\d+)批(\d+)\.md$")

# ── 第一步/第二步产物必备小节（模板见 harness 主文档）────────────────
_REQUIRED_HEADINGS: dict[str, list[str]] = {
    "世界观": ["核心冲突", "力量体系", "金手指", "大法则"],
    "总纲": ["结局锚点", "分卷表"],
    "卷纲": ["本卷定位", "本卷反派", "本卷期待", "大爽点布局表"],
}


def classify_outline_file(rel_path: str | Path) -> str | None:
    """相对 workspace 路径 → 'worldview' | 'outline' | 'chapters' | None。

    三步归属（写入白名单之外的第二道结构判定）：
    世界观.md 归 worldview；总纲/卷纲/character/core 归 outline；
    章纲批/期待台账/character/support 归 chapters；其余（含旧七步
    00-06 文件）返回 None。
    """
    p = Path(rel_path).as_posix().lstrip("/")
    if p.startswith("outline/"):
        name = p[len("outline/"):]
        if name == WORLDVIEW_FILENAME:
            return "worldview"
        if name == MASTER_FILENAME or VOLUME_FILE_PATTERN.match(name):
            return "outline"
        if BATCH_FILE_PATTERN.match(name) or name == LEDGER_FILENAME:
            return "chapters"
        return None
    if p.startswith("character/core/"):
        return "outline"
    if p.startswith("character/support/"):
        return "chapters"
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


def validate_worldview(text: str) -> list[str]:
    """outline/世界观.md：必备小节 + 金手指代价限制红线。"""
    errors: list[str] = []
    have = _headings(text)
    joined = "\n".join(have)
    for h in _REQUIRED_HEADINGS["世界观"]:
        if h not in joined:
            errors.append(f"[世界观] 缺必备小节「{h}」")
    m = re.search(r"代价与限制[：:]\s*(\S.*)", text)
    if not m or m.group(1).strip() in {"无", "待定", "暂无", "—", "-"}:
        errors.append("[世界观] 金手指「代价与限制」必须非空且不得为「无/待定」")
    return errors


def validate_design_artifact(stem: str, text: str) -> list[str]:
    """总纲/卷纲必备小节校验。stem ∈ {'总纲', '卷纲'}。"""
    required = _REQUIRED_HEADINGS.get(stem)
    if required is None:
        return []
    errors: list[str] = []
    have = _headings(text)
    joined = "\n".join(have)
    for h in required:
        if h not in joined:
            errors.append(f"[{stem}] 缺必备小节「{h}」")
    return errors


def validate_ledger(text: str) -> list[str]:
    """期待台账六列结构 + 词表 + 已放弃必带原因（DEC-010 / FR-005）。"""
    errors: list[str] = []
    ok, rows = _find_table(text, LEDGER_COLUMNS)
    if not ok:
        return ["[期待台账] 缺六列表（编号/层级/钩子/埋设章/兑现章/状态）"]
    for r in rows:
        if not r:
            continue
        rid = r[0] if r else ""
        if not LEDGER_ID_PATTERN.match(rid):
            errors.append(f"[期待台账] 编号不合法: {rid!r}（须 M-01/V-01 形式）")
        if len(r) > 1 and r[1] not in LEDGER_LEVELS:
            errors.append(f"[期待台账] 层级词不合法: {r[1]!r}（须 主线/卷）")
        if len(r) > 5:
            status = r[5].strip()
            if status.startswith("已放弃"):
                if not LEDGER_ABANDONED_PATTERN.match(status):
                    errors.append(f"[期待台账] {rid} 已放弃但状态列未写原因（格式：已放弃：原因）")
            elif status not in LEDGER_STATUSES:
                errors.append(f"[期待台账] 状态词不合法: {status!r}（须 活跃/已兑现/已放弃：原因）")
    return errors


def validate_chapter_batch(text: str) -> list[str]:
    """章纲批结构：前情接点 + 四列表 + 批内章号连续。"""
    errors: list[str] = []
    if not re.search(r"前情接点[：:]", text):
        errors.append("[章纲批] 缺「前情接点：」行")
    ok, rows = _find_table(text, BATCH_COLUMNS)
    if not ok:
        errors.append("[章纲批] 缺四列表（章/剧情走向/章末钩子/期待动作）")
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
    """按路径分派到对应校验器（guard 统一入口）。非三步产物返回空。"""
    p = Path(rel_path).as_posix().lstrip("/")
    name = Path(p).name
    if p == f"outline/{WORLDVIEW_FILENAME}":
        return validate_worldview(text)
    if p == f"outline/{LEDGER_FILENAME}":
        return validate_ledger(text)
    if p.startswith("outline/") and BATCH_FILE_PATTERN.match(name):
        return validate_chapter_batch(text)
    if p.startswith("outline/") and name == MASTER_FILENAME:
        return validate_design_artifact("总纲", text)
    if p.startswith("outline/") and VOLUME_FILE_PATTERN.match(name):
        return validate_design_artifact("卷纲", text)
    return []


def parse_chapter_rows(text: str) -> list[dict[str, str]]:
    """解析章纲批四列表为字典行（评估端共用；表缺失返回空）。"""
    _, rows = _find_table(text, BATCH_COLUMNS)
    keys = BATCH_COLUMNS
    return [
        {k: (r[i] if i < len(r) else "") for i, k in enumerate(keys)}
        for r in rows
        if r and r[0]
    ]


def parse_ledger_rows(text: str) -> list[dict[str, str]]:
    """解析期待台账六列表为字典行（评估端共用；表缺失返回空）。"""
    _, rows = _find_table(text, LEDGER_COLUMNS)
    keys = LEDGER_COLUMNS
    return [
        {k: (r[i] if i < len(r) else "") for i, k in enumerate(keys)}
        for r in rows
        if r and r[0]
    ]


__all__ = [
    "BATCH_COLUMNS",
    "BATCH_FILE_PATTERN",
    "LEDGER_ABANDONED_PATTERN",
    "LEDGER_COLUMNS",
    "LEDGER_FILENAME",
    "LEDGER_ID_PATTERN",
    "LEDGER_LEVELS",
    "LEDGER_STATUSES",
    "MASTER_FILENAME",
    "VOLUME_FILE_PATTERN",
    "WORLDVIEW_FILENAME",
    "classify_outline_file",
    "parse_chapter_rows",
    "parse_ledger_rows",
    "validate_chapter_batch",
    "validate_design_artifact",
    "validate_ledger",
    "validate_outline_file",
    "validate_worldview",
]
