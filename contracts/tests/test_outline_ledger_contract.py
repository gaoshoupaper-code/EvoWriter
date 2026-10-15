"""三步产物结构契约测试（REQ-20261015-161200 FR-001/004/005，DEC-004/008/010）。"""
from __future__ import annotations

from contracts.outline_ledger_contract import (
    classify_outline_file,
    parse_chapter_rows,
    parse_ledger_rows,
    validate_chapter_batch,
    validate_design_artifact,
    validate_ledger,
    validate_outline_file,
    validate_worldview,
)

VALID_WORLDVIEW = """# 世界观

## 核心冲突
- 宗门以功法定阶，杂役林凡被夺功法后只剩黑碑
- 大 BOSS 掌律宗主要收走黑碑，林凡要靠黑碑活命

## 力量体系
| 境界 | 层数 | 战力意象 |
|---|---|---|
| 炼气 | 9 | 御物 |
| 筑基 | 3 | 破空 |

## 金手指
- 是什么：黑碑可吞噬功法化为己用
- 怎么用：接触目标功法玉简三息内完成吞噬
- 代价与限制：每次吞噬反噬三日，同源功法吞噬无效

## 大法则
- 天地灵气枯竭，功法即命脉
"""

VALID_MASTER = """# 总纲

## 结局锚点（草稿，随连载可修订）
- 大 BOSS 怎么倒：黑碑吞噬律宗主本源功法
- 主角终点状态：立新法统

## 分卷表
| 卷 | 境界段 | 核心冲突 | 卷级反派 | 大兑现 |
|---|---|---|---|---|
| 1 | 炼气 | 外门生存 | 执法堂主 | 大比夺魁 |
"""

VALID_VOLUME = """# 第 1 卷：外门求生

## 本卷定位
- 境界段：炼气一至六层
- 本卷在全书的角色：立人设 + 立金手指 + 树第一个大敌

## 本卷反派
- 他是谁：执法堂主赵拔山
- 冲突点：他吞了林凡的功法
- 倒台方式：大比败露被废

## 本卷期待
| 期待内容 | 埋设章 | 兑现章 |
|---|---|---|
| 大比夺魁 | 3 | 卷末 |

## 大爽点布局表
| 章段 | 大爽点 | 类型 |
|---|---|---|
| 1-5 | 黑碑认主 | 获宝 |
"""

VALID_LEDGER = """# 期待台账

| 编号 | 层级 | 钩子 | 埋设章 | 兑现章 | 状态 |
|---|---|---|---|---|---|
| M-01 | 主线 | 黑碑里的封印之物 | 1 | 结局 | 活跃 |
| V-01 | 卷 | 外门大比夺魁 | 3 | 28 | 已兑现 |
| V-02 | 卷 | 被夺功法翻案 | 1 |  | 已放弃：第 30 章主角明示不再追究 |
"""

VALID_BATCH = """# 第 001-005 章（卷 1 批 1）

前情接点：开篇

| 章 | 剧情走向 | 章末钩子 | 期待动作 |
|---|---|---|---|
| 1 | 杂役林凡被夺功法，黑碑异动 | 危机钩：执法队上门 | 埋设 M-01 |
| 2 | 黑碑认主，首获吞噬之力 | 悬念钩：碑中低语 | 推进 M-01 |
| 3 | 首杀妖狼，反噬初显 | 期待钩：大比报名 | 埋设 V-01 |
"""


def test_worldview_valid() -> None:
    assert validate_worldview(VALID_WORLDVIEW) == []


def test_worldview_missing_heading() -> None:
    bad = VALID_WORLDVIEW.replace("## 大法则", "## 世界规则")
    assert any("大法则" in e for e in validate_worldview(bad))


def test_worldview_golden_finger_requires_cost() -> None:
    bad = VALID_WORLDVIEW.replace(
        "- 代价与限制：每次吞噬反噬三日，同源功法吞噬无效",
        "- 代价与限制：无",
    )
    errs = validate_worldview(bad)
    assert any("代价与限制" in e for e in errs)


def test_master_and_volume_headings() -> None:
    assert validate_design_artifact("总纲", VALID_MASTER) == []
    assert validate_design_artifact("卷纲", VALID_VOLUME) == []
    bad = VALID_MASTER.replace("## 结局锚点（草稿，随连载可修订）", "## 结局")
    assert any("结局锚点" in e for e in validate_design_artifact("总纲", bad))


def test_ledger_valid() -> None:
    assert validate_ledger(VALID_LEDGER) == []


def test_ledger_old_eight_columns_rejected() -> None:
    old_style = VALID_LEDGER.replace(
        "| 编号 | 层级 | 钩子 | 埋设章 | 兑现章 | 状态 |",
        "| ID | 层级 | 期待内容 | 埋设章 | 最近推进章 | 计划兑现章 | 状态 | 备注 |",
    )
    assert validate_ledger(old_style) != []


def test_ledger_bad_id_level() -> None:
    bad = VALID_LEDGER.replace("| M-01 | 主线 |", "| X1 | 全书 |")
    errs = validate_ledger(bad)
    assert any("编号" in e for e in errs)
    assert any("层级" in e for e in errs)


def test_ledger_bad_status_word() -> None:
    bad = VALID_LEDGER.replace(
        "| V-01 | 卷 | 外门大比夺魁 | 3 | 28 | 已兑现 |",
        "| V-01 | 卷 | 外门大比夺魁 | 3 | 28 | 完成 |",
    )
    assert any("状态" in e for e in validate_ledger(bad))


def test_ledger_abandoned_requires_reason() -> None:
    bad = VALID_LEDGER.replace(
        "| V-02 | 卷 | 被夺功法翻案 | 1 |  | 已放弃：第 30 章主角明示不再追究 |",
        "| V-02 | 卷 | 被夺功法翻案 | 1 |  | 已放弃 |",
    )
    errs = validate_ledger(bad)
    assert any("已放弃" in e and "原因" in e for e in errs)


def test_chapter_batch_valid() -> None:
    assert validate_chapter_batch(VALID_BATCH) == []


def test_chapter_batch_missing_context_line() -> None:
    bad = VALID_BATCH.replace("前情接点：开篇", "承接上文")
    assert any("前情接点" in e for e in validate_chapter_batch(bad))


def test_chapter_batch_discontinuous_numbers() -> None:
    bad = VALID_BATCH.replace("| 3 | 首杀妖狼", "| 5 | 首杀妖狼")
    assert any("不连续" in e for e in validate_chapter_batch(bad))


def test_chapter_batch_old_five_columns_rejected() -> None:
    old_style = VALID_BATCH.replace(
        "| 章 | 剧情走向 | 章末钩子 | 期待动作 |",
        "| 章 | 主要事件 | 爽点 | 章末钩子 | 期待推进 |",
    )
    assert validate_chapter_batch(old_style) != []


def test_classify_outline_file() -> None:
    assert classify_outline_file("outline/世界观.md") == "worldview"
    assert classify_outline_file("outline/总纲.md") == "outline"
    assert classify_outline_file("outline/卷纲-第2卷.md") == "outline"
    assert classify_outline_file("character/core/赵拔山.md") == "outline"
    assert classify_outline_file("outline/章纲-卷1批2.md") == "chapters"
    assert classify_outline_file("outline/期待台账.md") == "chapters"
    assert classify_outline_file("character/support/小翠.md") == "chapters"
    assert classify_outline_file("outline/00-赛道.md") is None
    assert classify_outline_file("demand.md") is None


def test_validate_outline_file_dispatch() -> None:
    assert validate_outline_file("outline/世界观.md", VALID_WORLDVIEW) == []
    assert validate_outline_file("outline/期待台账.md", VALID_LEDGER) == []
    assert validate_outline_file("outline/章纲-卷1批1.md", VALID_BATCH) == []
    assert validate_outline_file("outline/卷纲-第1卷.md", VALID_VOLUME) == []
    assert validate_outline_file("outline/总纲.md", VALID_MASTER) == []
    # 旧七步产物文件不校验（legacy workspace 只读）
    assert validate_outline_file("outline/04-总纲.md", "任意内容") == []


def test_parse_rows() -> None:
    ledger = parse_ledger_rows(VALID_LEDGER)
    assert ledger[0]["编号"] == "M-01"
    assert ledger[0]["钩子"] == "黑碑里的封印之物"
    batch = parse_chapter_rows(VALID_BATCH)
    assert batch[0]["章"] == "1"
    assert batch[0]["章末钩子"] == "危机钩：执法队上门"
