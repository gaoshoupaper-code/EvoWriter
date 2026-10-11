"""outline 产物结构契约测试（REQ-20261011-142529 FR-001/005/006，DEC-004/007/017）。"""
from __future__ import annotations

from contracts.outline_ledger_contract import (
    classify_outline_file,
    validate_chapter_batch,
    validate_design_artifact,
    validate_ledger,
    validate_outline_file,
)

VALID_LEDGER = """# 期待台账

| ID | 层级 | 期待内容 | 埋设章 | 最近推进章 | 计划兑现章 | 状态 | 备注 |
|---|---|---|---|---|---|---|---|
| M-01 | 主线 | 黑碑里的封印之物 | 1 | 12 | 结局 | 活跃 |  |
| V-01 | 卷 | 黑碑里的封印之物 | 1 | 8 | 卷末 | 已兑现 |  |
| V-02 | 卷 | 外门大比夺魁 | 3 |  | 25 | 已放弃 | 第 30 章主角退赛明示 |
"""

VALID_BATCH = """# 第 001-005 章细纲（第 1 批）

前情接点：开篇

| 章 | 主要事件 | 爽点 | 章末钩子 | 期待推进 |
|---|---|---|---|---|
| 1 | 杂役林凡被夺功法 | 获宝（小） | 危机钩 | 埋设 M-01 |
| 2 | 黑碑认主 | 升级（小） | 悬念钩 | 推进 M-01 |
| 3 | 首杀妖狼 | 打脸（大） | 挑衅钩 |  |
"""

VALID_DESIGN_01 = """# 设计原则

## 定稿一句话
> 宗门杂役偷学禁术，以吞噬功法爬到诸天之巅。

## 四要素对照
| 要素 | 内容 |
|---|---|

## 场景推演
1. 场景一

## 爽点预感
一句话：装逼打脸密度拉满。
"""

VALID_POWER_03 = """# 力量体系

## 境界划分
炼气 × 九层

## 金手指规则
- 是什么：黑碑
- 怎么用：吞噬功法
- 代价与限制：每吞噬一次折寿一年

## 升级-分卷对应表
| 卷 | 境界段 |
|---|---|
"""


def test_ledger_valid():
    assert validate_ledger(VALID_LEDGER) == []


def test_ledger_missing_table():
    assert any("八列表" in e for e in validate_ledger("# 期待台账\n空文件"))


def test_ledger_bad_words_and_abandoned_without_note():
    text = VALID_LEDGER.replace("| V-02 | 卷 | 外门大比夺魁 | 3 |  | 25 | 已放弃 | 第 30 章主角退赛明示 |",
                                "| X-2 | 线级 | 外门大比夺魁 | 3 |  | 25 | 已放弃 |  |")
    errors = validate_ledger(text)
    assert any("ID 不合法" in e for e in errors)
    assert any("层级词不合法" in e for e in errors)
    assert any("已放弃但备注为空" in e for e in errors)


def test_batch_valid():
    assert validate_chapter_batch(VALID_BATCH) == []


def test_batch_missing_anchor_and_gap():
    no_anchor = VALID_BATCH.replace("前情接点：开篇", "开篇即冲突")
    assert any("前情接点" in e for e in validate_chapter_batch(no_anchor))
    gap = VALID_BATCH.replace("| 3 | 首杀妖狼", "| 9 | 首杀妖狼")
    assert any("章号不连续" in e for e in validate_chapter_batch(gap))


def test_design_artifact_valid_and_missing_heading():
    assert validate_design_artifact("01-设计原则", VALID_DESIGN_01) == []
    errors = validate_design_artifact("01-设计原则", VALID_DESIGN_01.replace("## 爽点预感", "## 预感"))
    assert any("爽点预感" in e for e in errors)


def test_power_cost_must_be_nonempty():
    empty_cost = VALID_POWER_03.replace("代价与限制：每吞噬一次折寿一年", "代价与限制：无")
    assert any("代价与限制" in e for e in validate_design_artifact("03-力量体系", empty_cost))
    assert validate_design_artifact("03-力量体系", VALID_POWER_03) == []


def test_classify_outline_file():
    assert classify_outline_file("outline/01-设计原则.md") == "design"
    assert classify_outline_file("outline/04-总纲.md") == "structure"
    assert classify_outline_file("outline/期待台账.md") == "structure"
    assert classify_outline_file("outline/rework/001-提案.md") == "structure"
    assert classify_outline_file("character/core/林凡.md") == "design"
    assert classify_outline_file("character/support/王执事.md") == "structure"
    assert classify_outline_file("worldview/core.md") == "design"
    assert classify_outline_file("worldview/setting.md") == "structure"
    assert classify_outline_file("storyline.md") is None


def test_validate_outline_file_dispatch():
    assert validate_outline_file("outline/期待台账.md", VALID_LEDGER) == []
    assert validate_outline_file("outline/06-章纲/第01卷/第001-005章.md", VALID_BATCH) == []
    assert validate_outline_file("outline/00-赛道.md", "# 赛道\n- 平台：番茄\n- 品类：玄幻") == []
