"""输出风格规则检查器测试（REQ-20261015-161200 FR-004，AC-003）。"""
from __future__ import annotations

from contracts.style_rules import check_style_rules


def test_clean_text_passes() -> None:
    text = "# 世界观\n\n## 核心冲突\n- 杂役被夺功法\n- 黑碑要收走\n"
    assert check_style_rules(text) == []


def test_dash_flagged() -> None:
    text = "- 黑碑认主——从此走上吞噬之路\n"
    violations = check_style_rules(text)
    assert any("——" in v for v in violations)


def test_lead_phrases_flagged() -> None:
    for phrase in ("这意味着", "也就是说", "换句话说", "需要注意的是", "值得注意的是"):
        text = f"金手指有限制。{phrase}主角不能无敌。\n"
        assert any(phrase in v for v in check_style_rules(text)), phrase


def test_multiple_violations_all_reported() -> None:
    text = "他赢了——第一次。也就是说，复仇开始了。\n"
    violations = check_style_rules(text)
    assert len(violations) == 2
