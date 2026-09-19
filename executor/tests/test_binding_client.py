"""binding_client 测试：bind 成功/降级、spool 补账、resume 门禁语义。

用 httpx.MockTransport 模拟 Platform。核心断言：
- bind 绝不抛异常（fail-static 红线）
- 成功路径写 known_production.json（脱敏，无明文 key）
- 失败路径写 spool 文件，replay 成功后清空
- resume_check：404→None；网络失败→保守 incompatible
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from app.platform.agent.binding_client import BindingClient
from contracts.platform import LlmConfigSnapshot

_SNAPSHOT = LlmConfigSnapshot(
    model="deepseek-chat", base_url="https://api.test/v1",
    api_key_ref=None, source="evolution",
)


def _ok_ack(created: bool = True) -> httpx.Response:
    return httpx.Response(200, json={
        "binding": {
            "trace_id": "trace-1",
            "manifest_id": 7,
            "harness_commit": "c" * 40,
            "llm_config": _SNAPSHOT.model_dump(),
            "run_purpose": "production",
            "degraded": False,
            "runtime_identity_digest": None,
            "bound_at": "2026-09-19T00:00:00Z",
            "status": "active",
        },
        "created": created,
    })


def _make_client(tmp: Path, handler) -> tuple[BindingClient, Path, Path]:
    spool = tmp / "spool"
    known = tmp / "known_production.json"
    client = BindingClient(
        "http://platform-test", spool, known,
        client=httpx.Client(base_url="http://platform-test", transport=httpx.MockTransport(handler)),
    )
    return client, spool, known


class BindTest(unittest.TestCase):
    def test_bind_success_writes_known_production(self) -> None:
        """bind 成功：返回 Platform 记录 + created；known_production.json 落盘脱敏摘要。"""
        with TemporaryDirectory() as tmp:
            client, spool, known = _make_client(
                Path(tmp), lambda request: _ok_ack(created=True),
            )
            record, created = client.bind(
                "trace-1", "c" * 40, _SNAPSHOT, "production",
            )
            self.assertTrue(created)
            self.assertFalse(record.degraded)
            self.assertEqual(record.manifest_id, 7)

            data = json.loads(known.read_text("utf-8"))
            self.assertEqual(data["commit"], "c" * 40)
            self.assertEqual(data["llm_config"]["model"], "deepseek-chat")
            # 红线：known_production 只有脱敏字段（api_key_ref 引用），无明文 key 字段
            self.assertNotIn("api_key", data["llm_config"])
            self.assertEqual(list(spool.glob("*.json")), [])

    def test_bind_failure_degraded_and_spooled(self) -> None:
        """bind 失败（5xx）：不抛异常，返回 degraded 记录，spool 文件存在。"""
        with TemporaryDirectory() as tmp:
            client, spool, known = _make_client(
                Path(tmp), lambda request: httpx.Response(503),
            )
            record, created = client.bind(
                "trace-2", "d" * 40, _SNAPSHOT, "production",
            )
            self.assertFalse(created)
            self.assertTrue(record.degraded)
            self.assertEqual(record.status, "active")
            self.assertTrue(record.bound_at)
            self.assertTrue((spool / "trace-2.json").exists())
            self.assertFalse(known.exists())  # 未成功，不写 known_production

            # spool 内容是完整 BindingCreate（补账可原样重发），
            # degraded 已置 True——补账=降级补签，Platform 端要记录事实（review #12）
            req = json.loads((spool / "trace-2.json").read_text("utf-8"))
            self.assertEqual(req["trace_id"], "trace-2")
            self.assertEqual(req["harness_commit"], "d" * 40)
            self.assertEqual(req["llm_config"]["model"], "deepseek-chat")
            self.assertTrue(req["degraded"])

    def test_bind_network_error_also_fail_static(self) -> None:
        """网络层异常（连接失败）同样降级，不抛异常。"""
        def _raise(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with TemporaryDirectory() as tmp:
            client, spool, _known = _make_client(Path(tmp), _raise)
            record, created = client.bind("trace-3", "e" * 40, None, "optimization")
            self.assertFalse(created)
            self.assertTrue(record.degraded)
            self.assertTrue((spool / "trace-3.json").exists())


class ReplayTest(unittest.TestCase):
    def test_replay_spooled_success_then_cleared(self) -> None:
        """replay：spool 中的补账逐条重发，成功后文件删除。"""
        state = {"fail": True}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/bindings" and state["fail"]:
                return httpx.Response(503)
            if request.url.path == "/api/bindings":
                return _ok_ack(created=True)
            return httpx.Response(404)

        with TemporaryDirectory() as tmp:
            client, spool, _known = _make_client(Path(tmp), handler)
            client.bind("trace-4", "f" * 40, _SNAPSHOT, "production")
            self.assertEqual(len(list(spool.glob("*.json"))), 1)

            state["fail"] = False
            replayed = client.replay_spooled()
            self.assertEqual(replayed, 1)
            self.assertEqual(list(spool.glob("*.json")), [])  # 补账成功即清空

            # 幂等：无 spool 时再跑一轮不报错
            self.assertEqual(client.replay_spooled(), 0)

    def test_replay_keeps_file_on_failure(self) -> None:
        """补账仍失败：spool 文件保留，等下一轮。"""
        with TemporaryDirectory() as tmp:
            client, spool, _known = _make_client(
                Path(tmp), lambda request: httpx.Response(500),
            )
            client.bind("trace-5", "a" * 40, None, "production")
            self.assertEqual(client.replay_spooled(), 0)
            self.assertTrue((spool / "trace-5.json").exists())


class ResumeCheckTest(unittest.TestCase):
    def test_resume_check_404_returns_none(self) -> None:
        """无绑定记录（legacy Run / 未签发）→ None，照旧恢复。"""
        with TemporaryDirectory() as tmp:
            client, _, _ = _make_client(
                Path(tmp), lambda request: httpx.Response(404),
            )
            self.assertIsNone(client.resume_check("trace-legacy"))

    def test_resume_check_network_failure_conservative_incompatible(self) -> None:
        """Platform 不可达 → 保守 incompatible（不返回 None，拒绝恢复）。"""
        def _raise(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        with TemporaryDirectory() as tmp:
            client, _, _ = _make_client(Path(tmp), _raise)
            check = client.resume_check("trace-6")
            self.assertIsNotNone(check)
            self.assertEqual(check.decision, "incompatible")
            self.assertIn("platform 不可达", check.reason)

    def test_resume_check_compatible_passthrough(self) -> None:
        """Platform 判定 compatible → 原样透传。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "trace_id": "trace-7",
                "decision": "compatible",
                "reason": "仅 a_text 层变化",
                "bound_commit": "b" * 40,
                "current_commit": "c" * 40,
            })

        with TemporaryDirectory() as tmp:
            client, _, _ = _make_client(Path(tmp), handler)
            check = client.resume_check("trace-7")
            self.assertEqual(check.decision, "compatible")

    def test_get_binding_returns_none_on_404_and_failure(self) -> None:
        state = {"fail": False}

        def handler(request: httpx.Request) -> httpx.Response:
            if state["fail"]:
                raise httpx.ConnectError("down")
            return httpx.Response(404)

        with TemporaryDirectory() as tmp:
            client, _, _ = _make_client(Path(tmp), handler)
            self.assertIsNone(client.get_binding("trace-8"))
            state["fail"] = True
            self.assertIsNone(client.get_binding("trace-8"))


class KnownProductionTest(unittest.TestCase):
    def test_get_known_production_roundtrip(self) -> None:
        """bind 成功写入后 get_known_production 读回摘要；缺失/损坏返回 None。"""
        state = {"fail": False}

        def handler(request: httpx.Request) -> httpx.Response:
            if state["fail"]:
                return httpx.Response(503)
            return _ok_ack(created=True)

        with TemporaryDirectory() as tmp:
            client, _spool, known = _make_client(Path(tmp), handler)
            self.assertIsNone(client.get_known_production())  # 尚未写入

            client.bind("trace-1", "c" * 40, _SNAPSHOT, "production")
            data = client.get_known_production()
            self.assertIsNotNone(data)
            self.assertEqual(data["commit"], "c" * 40)
            self.assertEqual(data["llm_config"]["model"], "deepseek-chat")
            self.assertNotIn("api_key", data["llm_config"])  # 脱敏红线

            # 文件损坏（非 JSON）→ None，不抛异常（回退链路自身 fail-static）
            known.write_text("not-json{", encoding="utf-8")
            self.assertIsNone(client.get_known_production())


if __name__ == "__main__":
    unittest.main()
