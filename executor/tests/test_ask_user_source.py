"""ask_user source 参数测试（REQ-20261009-224433 FR-003）。

source 随 interrupt payload 冒泡，经 SSE interrupt 事件透传，前端据此识别
「需求澄清」类问题并渲染「跳过澄清」入口（AC-004 的路由前置）。
不传 source 时 payload 保持旧形态（无 source 键），image 域等既有调用不受影响。
"""

from __future__ import annotations

from importlib import import_module

from app.platform.tools.ask_user import AskUserOption

# 包 __init__ 把 `ask_user` 名字 rebind 成了函数，import a.b as x 走属性链
# 拿到的是函数；import_module 才是模块本身。
ask_user_module = import_module("app.platform.tools.ask_user")


def _capture_interrupt(monkeypatch, captured: dict) -> None:
    def fake_interrupt(payload: dict) -> str:
        captured.update(payload)
        return "用户的回答"

    monkeypatch.setattr(ask_user_module, "interrupt", fake_interrupt)


def test_source_included_when_provided(monkeypatch) -> None:
    captured: dict = {}
    _capture_interrupt(monkeypatch, captured)
    options = [AskUserOption(label="系统流", description="升级体系清晰")]

    answer = ask_user_module.ask_user(
        "金手指走哪种体系？", options, source="demand-clarification"
    )

    assert answer == "用户的回答"
    assert captured["source"] == "demand-clarification"
    assert captured["question"] == "金手指走哪种体系？"


def test_source_absent_by_default(monkeypatch) -> None:
    captured: dict = {}
    _capture_interrupt(monkeypatch, captured)
    options = [AskUserOption(label="系统流", description="升级体系清晰")]

    ask_user_module.ask_user("金手指走哪种体系？", options)

    assert "source" not in captured
