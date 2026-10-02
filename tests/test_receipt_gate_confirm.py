"""回执轮 interrupt 暂停修复的回归测试（2026-10-02 线上死锁）。

死锁链条（修复前）：ReceiptGate 禁写（回执未确认）× ArtifactValidation 拦终局
（storyline.md 未产出）互锁——模型文本停轮被拦回，写入被拦截，重复模型调用
不终止，输入框全程锁死。

修复后语义：
  1. 回执轮经 confirm_with_user 工具（interrupt 挂起）等待用户确认
  2. 工具正常返回（= 用户已 resume）→ ReceiptGate 释放受保护写入
  3. GraphInterrupt 异常路径（挂起本身）不误标已确认
  4. 确认后回执轮指令停止注入（不再压制初构）
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from middleware.receipt_gate import ReceiptGateMiddleware  # noqa: E402
from tools.confirm_with_user import (  # noqa: E402
    ConfirmOption,
    build_confirm_payload,
)


def _write_request(file_path: str = "/storyline.md", call_id: str = "c1"):
    return SimpleNamespace(
        tool_call={
            "name": "write_file",
            "args": {"file_path": file_path, "content": "x"},
            "id": call_id,
        },
    )


def _confirm_request(call_id: str = "c9"):
    return SimpleNamespace(
        tool_call={
            "name": "confirm_with_user",
            "args": {"question": "回执", "options": [{"label": "确认", "description": ""}]},
            "id": call_id,
        },
    )


class ReceiptGateConfirmTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name).resolve()
        self.gate = ReceiptGateMiddleware(self.workspace)
        self.gate._run_count = 1  # 首次运行

    def tearDown(self):
        self._tmp.cleanup()

    def _blocked(self, request) -> bool:
        return self.gate._maybe_block(request) is not None

    def test_unconfirmed_first_run_blocks_writes(self):
        """未确认时受保护产物写入仍被拦截（旧行为保持）。"""
        self.assertTrue(self._blocked(_write_request("/storyline.md")))
        self.assertTrue(self._blocked(_write_request("/worldview.md")))
        self.assertTrue(self._blocked(_write_request("/character/a.md")))

    def test_confirm_return_releases_writes(self):
        """confirm_with_user 正常返回 = 用户已回复 → 写入释放。"""
        result = self.gate.wrap_tool_call(
            _confirm_request(), handler=lambda req: "用户确认"
        )
        self.assertEqual(result, "用户确认")
        self.assertTrue(self.gate._confirmed)
        self.assertFalse(self._blocked(_write_request("/storyline.md")))

    def test_graph_interrupt_does_not_confirm(self):
        """挂起（GraphInterrupt 抛出）不算确认——异常原样上抛，闸门保持。"""

        def _interrupt_handler(req):
            raise type("GraphInterrupt", (BaseException,), {})()

        with self.assertRaises(BaseException):
            self.gate.wrap_tool_call(_confirm_request(), handler=_interrupt_handler)
        self.assertFalse(self.gate._confirmed)
        self.assertTrue(self._blocked(_write_request("/storyline.md")))

    def test_directive_stops_after_confirm(self):
        """确认后回执轮指令停止注入（不再压制初构推进）。"""
        self.assertIsNotNone(self.gate._inject_receipt_directive())
        self.gate.wrap_tool_call(_confirm_request(), handler=lambda req: "ok")
        self.assertIsNone(self.gate._inject_receipt_directive())

    def test_directive_teaches_tool_path(self):
        """指令必须引导 confirm_with_user 工具（防模型退回文本停轮）。"""
        directive = self.gate._inject_receipt_directive()
        content = directive["messages"][0].content
        self.assertIn("confirm_with_user", content)
        self.assertIn("不要用纯文本回复", content)

    def test_second_run_still_releases(self):
        """兜底口径保持：第 2 次运行（用户回复触发）起写入放行。"""
        self.gate._run_count = 2
        self.assertFalse(self._blocked(_write_request("/storyline.md")))


class ConfirmPayloadTest(unittest.TestCase):
    def test_payload_shape(self):
        """interrupt payload 按 hitl 协议带 kind/source，前端可路由渲染。"""
        payload = build_confirm_payload(
            "回执全文",
            [ConfirmOption(label="确认草案", description="开始初构")],
        )
        self.assertEqual(payload["kind"], "choice")
        self.assertEqual(payload["source"], "storybuilding-receipt")
        self.assertEqual(payload["question"], "回执全文")
        self.assertEqual(payload["options"][0]["label"], "确认草案")
        self.assertFalse(payload["multi_select"])


if __name__ == "__main__":
    unittest.main()
