"""storyline.md 新格式结构契约测试（REQ-20260930-194437）。

``assert_storyline_v2_contract`` 已迁至 ``contracts.storyline_contract``
（判定器唯一实现，运行时护栏与测试共用）；本文件保留终稿契约的行为测试。
"""

from __future__ import annotations

from pathlib import Path

from contracts.storybuilding_quota import count_line_block_headers
from contracts.storyline_contract import assert_storyline_v2_contract

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
