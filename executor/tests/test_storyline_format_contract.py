"""storyline.md 新格式结构契约（REQ-20260930-002231 AC-001 断言工具）。

``assert_storyline_v2_contract(md)`` 对任意 storyline.md 文本断言 FR-001 全部结构
约束，返回违规列表（空=合规）。本地用规范样例自测；线上验收时对真实产物
跑同一函数（AC-001 的「格式断言脚本」）。
"""

from __future__ import annotations

import re
from pathlib import Path

# 复用三处同构正则的判定器源头（contracts 唯一实现）
from contracts.storybuilding_quota import LINE_TYPES, count_line_block_headers

_BLOCK_HEADER = re.compile(
    r"(?m)^##\s+([^#·•\n]+?)\s*[·•]\s*\**(" + "|".join(LINE_TYPES) + r")\**(?:\s*[·•]|\s*$)"
)
_T_NUM = re.compile(r"^[Tt]\s*\d+(?:\.\d+)?$")
_LEGACY_IDS = re.compile(r"\b[Ss]\d{2}\b|\b[Ee]\d{3}\b|\b[Gg]\d{2}\b")
_LEGACY_FIELDS = re.compile(r"(?m)^(?:-\s*)?(?:\*\*)?(事件组|所属故事线|关键事件)(?:\*\*)?\s*[：:]")
_TABLE_HEADER_HINT = re.compile(r"^\|.*时序.*\|.*事件.*\|")


def assert_storyline_v2_contract(md: str) -> list[str]:
    """校验 storyline.md 是否满足 FR-001 结构契约，返回违规描述列表。"""
    violations: list[str] = []

    if not md.strip():
        return ["storyline.md 为空"]

    # 1. 至少一个线区块头
    headers = list(_BLOCK_HEADER.finditer(md))
    if not headers:
        violations.append("未找到线区块头（## {线名} · {类型} · {状态}）")

    # 2. 编号残留（S01 / E001 / G12）
    if _LEGACY_IDS.search(md):
        violations.append("存在 S/E/G 旧编号残留（名称即锚点，不应有编号）")

    # 3. 旧字段残留（事件组/所属故事线/关键事件）
    if _LEGACY_FIELDS.search(md):
        violations.append("存在旧字段残留（事件组/所属故事线/关键事件）")

    # 4. 逐区块校验：线头两字段 + 事件表八列 + 时序合法
    for i, m in enumerate(headers):
        name = m.group(1).strip()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(md)
        block = md[m.start():end]

        if "主要地点" not in block:
            violations.append(f"区块「{name}」缺线头字段「主要地点」")
        if "全局走向" not in block:
            violations.append(f"区块「{name}」缺线头字段「全局走向」")

        rows = [ln.strip() for ln in block.splitlines() if ln.strip().startswith("|")]
        data_rows = [
            ln for ln in rows
            if not _TABLE_HEADER_HINT.match(ln) and not re.fullmatch(r"\|[\s|:-]+\|", ln)
        ]
        header_row = next((ln for ln in rows if _TABLE_HEADER_HINT.match(ln)), None)
        if header_row is None:
            violations.append(f"区块「{name}」缺事件表（表头须含「时序」「事件」列）")
            continue
        if len(data_rows) == 0:
            violations.append(f"区块「{name}」事件表无数据行")
            continue

        expect_cols = header_row.strip().strip("|").count("|") + 1
        for ln in data_rows:
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) != expect_cols:
                violations.append(f"区块「{name}」事件行列数不齐：{ln[:40]}…")
                break
            t_raw = cells[0].replace("**", "")
            if t_raw and not _T_NUM.match(t_raw):
                violations.append(f"区块「{name}」时序号非法（{t_raw}，应为 T1/T12.5 形式）")
                break

    # 5. 名称唯一：线名与事件名不得重复
    line_names = [m.group(1).strip().replace("**", "") for m in headers]
    if len(line_names) != len(set(line_names)):
        violations.append("线名重复")
    event_names: list[str] = []
    for i, m in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(md)
        block = md[m.start():end]
        rows = [ln.strip() for ln in block.splitlines() if ln.strip().startswith("|")]
        for ln in rows:
            if _TABLE_HEADER_HINT.match(ln) or re.fullmatch(r"\|[\s|:-]+\|", ln):
                continue
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[1]:
                event_names.append(cells[1].replace("**", ""))
    if len(event_names) != len(set(event_names)):
        violations.append("事件名重复")

    return violations


_VALID_SAMPLE = """# 故事核心

- Logline：修士逆天改命
- 最终结局：重铸天道

## 复仇线 · 主线 · 活跃

- 主要地点：青云宗、九天神域
- 全局走向：从家族弃子到执掌天道

| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 描述 |
|------|------|------|------|------|------|------|------|
| T1 | 灭门之夜 | 冲突 | 发展 | 青云宗 | 林寒 | | 一夜之间家破人亡。 |
| T2.5 | 祭祖大典的闯入 | 冲突 | 发展 | 青云宗 | 林寒 | 感情线 | 当众闯坛。 |

## 感情线 · 支线 · 活跃

- 主要地点：云岚城
- 全局走向：并肩成长

| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 描述 |
|------|------|------|------|------|------|------|------|
| T2 | 初次相遇 | 悬念 | 发展 | 云岚城 | 林寒、苏晚 | | 缺口撕开。 |
"""


def test_valid_sample_passes_contract() -> None:
    assert assert_storyline_v2_contract(_VALID_SAMPLE) == []


def test_contract_reports_each_violation() -> None:
    # 无区块
    assert any("线区块头" in v for v in assert_storyline_v2_contract("# 只有核心\n"))
    # 编号 + 旧字段残留
    bad = _VALID_SAMPLE.replace("## 复仇线 · 主线 · 活跃", "## S01-复仇线 · 主线 · 活跃")
    assert any("旧编号" in v for v in assert_storyline_v2_contract(bad))
    bad2 = _VALID_SAMPLE.replace("- 主要地点：青云宗、九天神域", "- 关键事件：E001, E003")
    assert any("旧字段" in v for v in assert_storyline_v2_contract(bad2))
    # 缺线头字段
    bad3 = _VALID_SAMPLE.replace("- 全局走向：从家族弃子到执掌天道\n", "")
    assert any("全局走向" in v for v in assert_storyline_v2_contract(bad3))
    # 时序非法
    bad4 = _VALID_SAMPLE.replace("| T1 | 灭门之夜", "| 4月20日 | 灭门之夜")
    assert any("时序号非法" in v for v in assert_storyline_v2_contract(bad4))
    # 事件名重复
    bad5 = _VALID_SAMPLE.replace("初次相遇", "灭门之夜")
    assert any("事件名重复" in v for v in assert_storyline_v2_contract(bad5))


def test_contract_on_generated_sample_file(tmp_path: Path) -> None:
    """端到端小闭环：契约函数可作用于磁盘上的 storyline.md。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "storyline.md").write_text(_VALID_SAMPLE, encoding="utf-8")
    md = (ws / "storyline.md").read_text(encoding="utf-8")
    assert assert_storyline_v2_contract(md) == []
    assert count_line_block_headers(md) == 2
