"""每轮任务拍板闸门的回归测试（#7 一段式 + #8 零注入 + #10 每轮泛化）。

历史死锁链条（#5 修复前）：ReceiptGate 禁写（澄清未确认）× ArtifactValidation
拦终局（storyline.md 未产出）互锁——模型文本停轮被拦回，写入被拦截，重复模型
调用不终止，输入框全程锁死。#7/#8 已修：拍板经 confirm_with_user（interrupt
挂起）交互，一次有效回复即释放。

现行语义（进化 #10 每轮拍板硬闸）：
  1. 每轮运行（用户的每次新消息）在 confirm_with_user 一次有效返回前，
     拦截一切受保护产物写入（storyline / worldview / character/* / object/*）
  2. confirm_with_user 正常返回 = 用户拍板 → 本轮放行到底；用户明示跳过短语
     （「不用问了直接写」等）→ 立即释放
  3. GraphInterrupt 异常路径（挂起本身）不误标已确认
  4. before_agent 每轮重置：新运行（新任务）重新拍板——上轮拍板不跨轮生效
  5. storyline.md 已存在不再释放闸门（每轮任务都要重新拍板，与工作区状态
     解耦）；重复读一次性指路保留（第 2 次读取同文件加指路头，第 3 次起放行）
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
from langchain_core.messages import ToolMessage  # noqa: E402
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


def _read_request(file_path: str = "/demand.md", call_id: str = "r1"):
    return SimpleNamespace(
        tool_call={
            "name": "read_file",
            "args": {"file_path": file_path},
            "id": call_id,
        },
    )


def _read_handler(req):
    return ToolMessage(content="文件内容", name="read_file", tool_call_id="r1")


class ReceiptGateConfirmTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name).resolve()
        self.gate = ReceiptGateMiddleware(self.workspace)

    def tearDown(self):
        self._tmp.cleanup()

    def _blocked(self, request) -> bool:
        return self.gate._maybe_block(request) is not None

    def test_unconfirmed_blocks_writes(self):
        """未拍板时四类受保护产物写入一律被拦截（#10 含 object/*）。"""
        self.assertTrue(self._blocked(_write_request("/storyline.md")))
        self.assertTrue(self._blocked(_write_request("/worldview.md")))
        self.assertTrue(self._blocked(_write_request("/character/a.md")))
        self.assertTrue(self._blocked(_write_request("/object/青锋剑.md")))

    def test_confirm_return_releases_writes(self):
        """confirm_with_user 一次正常返回 = 提案拍板完成（一段式）→ 写入释放。"""
        first = self.gate.wrap_tool_call(
            _confirm_request(), handler=lambda req: "选定方案 1"
        )
        self.assertEqual(first, "选定方案 1")
        self.assertTrue(self.gate._confirmed)  # 一次有效回复即拍板
        self.assertFalse(self._blocked(_write_request("/storyline.md")))

    def test_skip_phrase_releases_immediately(self):
        """提案回复命中跳过短语（不用问了直接写）→ 立即释放写入。"""
        self.gate.wrap_tool_call(
            _confirm_request(), handler=lambda req: "不用问了，直接写"
        )
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

    def test_repeat_read_guided_once_then_passthrough(self):
        """提案轮重复读指路：第 2 次读取加一次性指路头，第 3 次起放行。"""
        first = self.gate.wrap_tool_call(
            _read_request("/demand.md"), handler=_read_handler
        )
        self.assertFalse(str(first.content).startswith("[提案闸门"))
        second = self.gate.wrap_tool_call(
            _read_request("/demand.md"), handler=_read_handler
        )
        self.assertIn("[提案闸门·重复读]", str(second.content))
        self.assertIn("confirm_with_user", str(second.content))
        self.assertIn("文件内容", str(second.content))  # 全文照给，不拦截读取
        third = self.gate.wrap_tool_call(
            _read_request("/demand.md"), handler=_read_handler
        )
        self.assertEqual(str(third.content), "文件内容")  # 第 3 次起放行

    def test_repeat_read_guidance_disabled_after_confirm(self):
        """拍板后重复读不再加指路头（正当核对不受打扰）。"""
        self.gate.wrap_tool_call(
            _confirm_request(), handler=lambda req: "选定方案 1"
        )
        for _ in range(3):
            result = self.gate.wrap_tool_call(
                _read_request("/demand.md"), handler=_read_handler
            )
            self.assertEqual(str(result.content), "文件内容")

    def test_storyline_exists_still_blocks_until_confirmed(self):
        """storyline.md 已存在（增量轮）→ 未拍板仍拦截（#10 每轮口径，
        与工作区状态解耦）；拍板后放行。"""
        (self.workspace / "storyline.md").write_text(
            "# 已有故事线", encoding="utf-8"
        )
        self.assertTrue(self._blocked(_write_request("/storyline.md")))
        self.gate.wrap_tool_call(
            _confirm_request(), handler=lambda req: "确认任务卡"
        )
        self.assertFalse(self._blocked(_write_request("/storyline.md")))

    def test_before_agent_resets_confirmation(self):
        """before_agent 每轮重置：上轮拍板不跨轮生效（#10）。"""
        self.gate.wrap_tool_call(
            _confirm_request(), handler=lambda req: "选定方案 1"
        )
        self.assertTrue(self.gate._confirmed)
        self.gate.before_agent(SimpleNamespace(), SimpleNamespace())
        self.assertFalse(self.gate._confirmed)
        self.assertTrue(self._blocked(_write_request("/storyline.md")))


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
