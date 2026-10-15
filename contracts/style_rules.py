"""输出风格规则（REQ-20261015-161200 FR-004，DEC-004）。

5 条规则写进 harness 全部 Prompt（主控 + 三子代理），约束一切落盘产物与
拍板消息。规则①②可程序检查——本模块是唯一判定实现（沿用契约判定器
单点原则）；③④⑤靠模板结构约束（契约必备小节/表格）与人工抽查。

规则清单：
  ① 禁破折号（——）补充解释
  ② 禁解释性引导语（这意味着/也就是说/换句话说/需要注意的是/值得注意的是）
  ③ 每条设定一句话、列表化、不写连贯段落
  ④ 卷纲/章纲/台账一律用表格
  ⑤ 不写设计理由，只给设定本身
"""
from __future__ import annotations

import re

# 规则①：破折号（成对全角 em dash）
_DASH_PATTERN = re.compile("——")

# 规则②：解释性引导语（命中即违例；中英文冒号变体不区分）
_LEAD_PHRASES = (
    "这意味着",
    "也就是说",
    "换句话说",
    "需要注意的是",
    "值得注意的是",
)

# 违例信息前缀（供检查器调用方拼接定位）
RULE_LABELS = {
    "dash": "禁破折号（——）补充解释",
    "lead": "禁解释性引导语",
}


def check_style_rules(text: str) -> list[str]:
    """扫描文本，返回风格规则①②违例列表（空列表 = 通过）。

    只做程序可判定的两条；供验收检查器与评估端共用。
    """
    violations: list[str] = []
    for m in _DASH_PATTERN.finditer(text):
        violations.append(f"{RULE_LABELS['dash']}：出现「——」")
        break  # 同类违例报一次，避免刷屏
    for phrase in _LEAD_PHRASES:
        if phrase in text:
            violations.append(f"{RULE_LABELS['lead']}：出现「{phrase}」")
    return violations


__all__ = ["RULE_LABELS", "check_style_rules"]
