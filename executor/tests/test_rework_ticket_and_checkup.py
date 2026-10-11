"""回炉工单工具与体检计算测试（REQ-20261011-142529 FR-004/FR-007，DEC-008/016）。

submit_rework_ticket：批准 → 落盘工单 + 管线状态 design_revising；
否决 → 不落盘不置状态。interrupt 由本测试 monkeypatch 模拟用户答复。
checkup：红项体检（缺爽点/大爽点超限/悬置/境界余量）+ 空工作区空报告。
"""
from __future__ import annotations

import json
import importlib
import json
from pathlib import Path

from contracts.outline_pipeline import (
    REWORK_DESIGN,
    load_pipeline_state,
)

_HARNESS = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"


def _ticket_module():
    """加载（幂等）harness 包与工单模块；直接对模块对象断言/打补丁。

    注意：load_package 每次调用会重新执行包 __init__（父模块属性链重置），
    因此 monkeypatch 必须用模块对象而不是字符串路径。
    """
    from app.platform.agent.loader import load_package

    load_package(_HARNESS)
    return importlib.import_module("harness_current.tools.submit_rework_ticket")


def _run_ticket(tmp_path: Path, monkeypatch, answer: str) -> str:
    ticket_mod = _ticket_module()
    tool = ticket_mod.build_rework_ticket_tool(tmp_path)
    monkeypatch.setattr(ticket_mod, "interrupt", lambda _payload: answer, raising=True)
    return tool.invoke({
        "title": "境界段不足以支撑第 3 卷",
        "proposal": "## 改什么\n03 力量体系追加一境。## 为什么\n防崩三问第三问不足。",
        "impact_files": ["outline/05-卷纲/第03卷.md"],
    })


def test_ticket_approved_writes_file_and_state(tmp_path, monkeypatch):
    result = _run_ticket(tmp_path, monkeypatch, "批准，转设计端修订")
    ticket = tmp_path / "outline" / "rework" / "001-提案.md"
    assert ticket.is_file()
    assert "境界段" in ticket.read_text(encoding="utf-8")
    state = load_pipeline_state(tmp_path)
    assert state.rework is not None
    assert state.rework.ticket == "001"
    assert state.rework.stage == REWORK_DESIGN
    assert "不要继续写任何产物" in result


def test_ticket_rejected_no_artifacts(tmp_path, monkeypatch):
    result = _run_ticket(tmp_path, monkeypatch, "否决，自行消化")
    assert not (tmp_path / "outline" / "rework").exists()
    assert load_pipeline_state(tmp_path).rework is None
    assert "自行消化" in result or "台账" in result


def test_ticket_ids_increment(tmp_path, monkeypatch):
    _run_ticket(tmp_path, monkeypatch, "批准，转设计端修订")
    # 模拟设计端完成第一单（状态清空但工单文件留档）
    state = load_pipeline_state(tmp_path)
    state.rework = None
    from contracts.outline_pipeline import save_pipeline_state

    save_pipeline_state(tmp_path, state)
    _run_ticket(tmp_path, monkeypatch, "批准，转设计端修订")
    assert (tmp_path / "outline" / "rework" / "002-提案.md").is_file()


# ── 体检计算（FR-007，DEC-007/016）─────────────────────────────


def _checkup_module():
    from app.platform.agent.loader import load_package

    load_package(_HARNESS)
    return importlib.import_module("harness_current.checkup")


def _mk_workspace(tmp_path: Path) -> None:
    outline = tmp_path / "outline"
    (outline / "06-章纲" / "第01卷").mkdir(parents=True, exist_ok=True)
    (outline / "00-赛道.md").write_text("# 赛道\n- 平台：番茄\n- 品类：玄幻\n", encoding="utf-8")
    (outline / "期待台账.md").write_text(
        "# 期待台账\n\n"
        "| ID | 层级 | 期待内容 | 埋设章 | 最近推进章 | 计划兑现章 | 状态 | 备注 |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| M-01 | 主线 | 黑碑封印之物 | 1 | 2 | 结局 | 活跃 |  |\n",
        encoding="utf-8",
    )
    (outline / "06-章纲" / "第01卷" / "第001-003章.md").write_text(
        "# 第 001-003 章细纲（第 1 批）\n前情接点：开篇\n"
        "| 章 | 主要事件 | 爽点 | 章末钩子 | 期待推进 |\n|---|---|---|---|---|\n"
        "| 1 | 被夺功法 | 获宝（小） | 危机钩 | 埋设 M-01 |\n"
        "| 2 | 黑碑认主 |  | 悬念钩 | 推进 M-01 |\n"
        "| 3 | 首杀妖狼 | 打脸（大） | 挑衅钩 |  |\n",
        encoding="utf-8",
    )


def test_checkup_flags_red_items(tmp_path):
    checkup = _checkup_module()
    _mk_workspace(tmp_path)
    report = checkup.compute_checkup(tmp_path, {})
    assert "程序体检报告" in report
    assert "🔴 缺失：第 2 章" in report          # 小爽点缺失
    assert "无悬置" in report                    # 台账健康（推进章 2 距第 3 章 1 章）
    assert "境界余量" not in report              # 03 未产出 → 该节省略


def test_checkup_stale_expectation_red(tmp_path):
    checkup = _checkup_module()
    _mk_workspace(tmp_path)
    ledger = tmp_path / "outline" / "期待台账.md"
    ledger.write_text(
        ledger.read_text(encoding="utf-8").replace("| 1 | 2 |", "| 1 |  |"),
        encoding="utf-8",
    )
    # 台账推进章空 → 回落埋设章 1；无 03 → 境界余量节约略，悬置按 30 章阈值不红（仅 3 章）
    report = checkup.compute_checkup(tmp_path, {})
    assert "[期待台账]" in report


def test_checkup_empty_workspace_returns_empty(tmp_path):
    checkup = _checkup_module()
    assert checkup.compute_checkup(tmp_path, {}) == ""
    assert checkup.render_checkup_section(tmp_path, Path(".")) == ""


def test_checkup_config_tracks(tmp_path):
    checkup = _checkup_module()
    config = checkup.load_checkup_config(
        Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"
    )
    assert config["tracks"]["番茄"]["max_big_payoff_gap"] == 5
    assert isinstance(json.dumps(config), str)
