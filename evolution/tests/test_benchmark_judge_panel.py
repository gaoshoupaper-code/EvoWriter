"""组评分引擎测试（REQ-20260930-162207 / FR-003/FR-004 / AC-004、AC-005，DEC-002/003/006/009）。

覆盖：
- 聚合口径：维度分 = 成功 judge 均分保留 1 位小数，总分 = 各维均分（AC-004）
- per-judge 明细：原始整数分 + 两段理由全量入 judge_group 块（DEC-006）
- 法定人数三分支：3 人组挂 1 → 降级出分；2 人组挂 1 → 行失败；1 人组 = 现状（AC-005）
- 缺席标注：失败 judge 该维 scores/reasons 记 None（DEC-003）
- 并发闸门机制：judge_gate 限制在途调用数（DEC-009 的 scorer 侧机制验证）
- judge 原始输出契约校验不放松（沿用 _validate_dim_judgement）
"""
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["EVOLUTION_MASTER_KEY"] = "a" * 64
os.environ["EXECUTOR_URL"] = "http://127.0.0.1:0"

# 注意：不在模块级 import app.*——模块级导入会让 app.core.settings 在
# EVOLUTION_DB 设置前实例化（fr007 等按环境变量建库的测试随后连错库）。
# app 导入一律放在测试方法内（与 test_benchmark_judge_select 同风格）。


def _raw(score: int) -> str:
    """构造合法单维 judge 返回（rubric v4 契约：score + 两段理由）。"""
    if score >= 5:
        flaws = ["未发现不足"]
    else:
        flaws = [f"第 {score} 档典型不足"]
    return json.dumps({"score": score, "达标": ["承诺点兑现"], "不足": flaws}, ensure_ascii=False)


DELIVERIES = {
    "主线 storyline": "x" * 300,
    "人物 character": "y" * 300,
    "世界观 worldview": "z" * 300,
}

JUDGES_3 = [
    {"config_id": 11, "name": "glm", "model": "glm-4.7", "fingerprint": "aa"},
    {"config_id": 22, "name": "kimi", "model": "kimi-k2", "fingerprint": "bb"},
    {"config_id": 33, "name": "deepseek", "model": "deepseek-v3", "fingerprint": "cc"},
]


def _chat_by_config(per_config: dict[int, object]):
    """按 config_id 分发返回值的 llm.chat 替身；值为例外则抛出。"""

    def _chat(messages, **kwargs):
        cid = kwargs.get("config_id")
        val = per_config[cid]
        if isinstance(val, Exception):
            raise val
        return val

    return _chat


class AggregationTest(unittest.TestCase):
    """AC-004：聚合口径与 per-judge 明细。"""

    def test_dimension_score_is_1_decimal_mean(self):
        from app.benchmark import rubric_v3, scorer
        # 三个 judge 对每维固定给 4 / 3 / 4 → 均分 11/3 = 3.666… → 3.7
        chat = _chat_by_config({11: _raw(4), 22: _raw(3), 33: _raw(4)})
        with patch.object(scorer.llm, "chat", side_effect=chat) as m:
            result = scorer.score_case(
                "demand", DELIVERIES, JUDGES_3,
                group_id=7, group_name="三人评审团",
            )
        self.assertEqual(m.call_count, 15, "5 维 × 3 judge = 15 次调用")
        for dim in rubric_v3.DIMENSION_KEYS:
            self.assertEqual(result["scores"][dim], 3.7)
        self.assertEqual(result["overall"], round(sum([3.7] * 5) / 5, 2))
        # 组信息块 + per-judge 原始整数分（DEC-002：小数只在聚合层）
        block = result["judge_group"]
        self.assertEqual(block["group_id"], 7)
        self.assertEqual(block["group_name"], "三人评审团")
        raw_by_cfg = {mem["config_id"]: mem for mem in block["members"]}
        self.assertEqual(raw_by_cfg[22]["scores"][rubric_v3.DIMENSION_KEYS[0]], 3)
        # 两段理由全量入明细（DEC-006）；顶层不再输出聚合理由
        self.assertNotIn("reasons", result)
        reasons0 = raw_by_cfg[11]["reasons"][rubric_v3.DIMENSION_KEYS[0]]
        self.assertEqual(reasons0["达标"], ["承诺点兑现"])

    def test_empty_judges_rejected(self):
        from app.benchmark import rubric_v3, scorer
        with self.assertRaises(ValueError):
            scorer.score_case("demand", DELIVERIES, [])


class QuorumTest(unittest.TestCase):
    """AC-005：法定人数三分支（DEC-003）。"""

    def test_three_judges_one_fails_degrades(self):
        from app.benchmark import rubric_v3, scorer
        chat = _chat_by_config(
            {11: _raw(4), 22: RuntimeError("kimi 端点超时"), 33: _raw(5)}
        )
        with patch.object(scorer.llm, "chat", side_effect=chat):
            result = scorer.score_case("demand", DELIVERIES, JUDGES_3)
        dim0 = rubric_v3.DIMENSION_KEYS[0]
        self.assertEqual(result["scores"][dim0], 4.5, "4/5 均分 4.5（缺席者不参与）")
        member = {m["config_id"]: m for m in result["judge_group"]["members"]}[22]
        self.assertIsNone(member["scores"][dim0], "失败 judge 该维记 None（缺席标注）")
        self.assertIsNone(member["reasons"][dim0])

    def test_two_judges_one_fails_row_fails(self):
        from app.benchmark import rubric_v3, scorer
        judges = JUDGES_3[:2]
        chat = _chat_by_config({11: _raw(4), 22: RuntimeError("挂")})
        with patch.object(scorer.llm, "chat", side_effect=chat):
            with self.assertRaises(scorer.DimensionScoreError) as ctx:
                scorer.score_case("demand", DELIVERIES, judges)
        msg = str(ctx.exception)
        self.assertIn("法定人数", msg)
        self.assertIn(rubric_v3.DIMENSION_KEYS[0], msg)
        self.assertIn("kimi", msg, "错误信息应点名缺席 judge")

    def test_single_judge_group_keeps_legacy_semantics(self):
        from app.benchmark import rubric_v3, scorer
        chat = _chat_by_config({11: _raw(5)})
        with patch.object(scorer.llm, "chat", side_effect=chat) as m:
            result = scorer.score_case("demand", DELIVERIES, JUDGES_3[:1])
        self.assertEqual(m.call_count, 5)
        dim0 = rubric_v3.DIMENSION_KEYS[0]
        self.assertEqual(result["scores"][dim0], 5.0)
        self.assertEqual(result["overall"], 5.0)

    def test_invalid_judge_output_still_rejected(self):
        from app.benchmark import rubric_v3, scorer
        """judge 原始输出契约不放松：非法分数重试用尽 → 行失败（风险条目）。"""
        bad = json.dumps({"score": 9, "达标": ["x"], "不足": ["y"]})
        chat = _chat_by_config({11: _raw(4), 22: bad, 33: _raw(4)})
        with patch.object(scorer.llm, "chat", side_effect=chat):
            # judge 22 非法 → 缺席；11+33 = 2 ≥ 法定人数 → 仍出分
            result = scorer.score_case("demand", DELIVERIES, JUDGES_3)
        dim0 = rubric_v3.DIMENSION_KEYS[0]
        self.assertEqual(result["scores"][dim0], 4.0)


class JudgeGateTest(unittest.TestCase):
    """DEC-009 scorer 侧机制：judge_gate 限制在途 llm 调用数。"""

    def test_gate_caps_inflight_calls(self):
        from app.benchmark import rubric_v3, scorer
        # 闸门设 2（小于 5 维 × 3 judge 的自然并发 15），验证机制生效
        gate = threading.Semaphore(2)
        in_flight = 0
        peak = 0
        lock = threading.Lock()

        def slow_chat(messages, **kwargs):
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            import time
            time.sleep(0.02)
            with lock:
                in_flight -= 1
            return _raw(4)

        with patch.object(scorer.llm, "chat", side_effect=slow_chat):
            result = scorer.score_case("demand", DELIVERIES, JUDGES_3, judge_gate=gate)
        self.assertLessEqual(peak, 2, f"在途峰值 {peak} 超过闸门 2")
        self.assertEqual(len(result["scores"]), 5, "闸门只限流不丢调用")


if __name__ == "__main__":
    unittest.main()
