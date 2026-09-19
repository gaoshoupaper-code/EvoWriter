"""单阶段发版流程测试（Phase A 平台化，REQ-20260919-202344）。

发版 = commit_candidate → Platform probe（门禁）→ Platform promote（账本晋升 +
artifact 打包 + executor reload 通知，Platform 全包）→ session=published。
本地 registry.json 只读：不注册 candidate、不移动 production 指针、不直连
executor reload。

覆盖：Platform 化成功路径（probe/promote 都打到 platform_url，零 executor
流量）、probe rejected 拒绝、promote 非 2xx → 502 激活失败、promote commit
一致性断言、legacy 幂等重入、旧线半途事件兼容、release_gate 直测、
rollback 经 Platform promote。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["EVOLUTION_DB"] = _tmp_db.name
os.environ["EXECUTOR_URL"] = "http://127.0.0.1:0"

import httpx  # noqa: E402
from contracts.platform import ProbeResult, PromoteResult  # noqa: E402

import app.core.db as db  # noqa: E402

# 测试用 Platform / executor 哨兵地址：断言发版流量只去前者
PLATFORM_URL = "http://platform-test:7790"
PROBE_URL = f"{PLATFORM_URL}/api/release/probe"
PROMOTE_URL = f"{PLATFORM_URL}/api/release/promote"
EXECUTOR_URL = "http://executor-test:7788"


def setUpModule() -> None:
    """硬隔离本模块的数据库。

    环境变量在模块 import 时设置，但全量跑时 settings 单例可能已被更早的
    测试模块抢先创建（指向 data/evolution.db 真实库），后设 env 无效——
    这里直接把 db 模块实际引用的 settings 实例的 evolution_db 换到独立
    临时库（facts/ingestion 测试同款手法），跑完恢复。
    """
    global _orig_evolution_db
    _orig_evolution_db = db.settings.evolution_db
    db.settings.evolution_db = _tmp_db.name
    db._conn = None
    db.init_db()


def tearDownModule() -> None:
    if db._conn is not None:
        db._conn.close()
    db._conn = None
    db.settings.evolution_db = _orig_evolution_db
    try:
        os.unlink(_tmp_db.name)
    except OSError:
        pass


def _platform_response(status_code: int, payload: dict) -> SimpleNamespace:
    """构造 release_gate 视角的 httpx.Response 替身。"""

    def _raise_for_status() -> None:
        if status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {status_code}", request=None, response=None
            )

    return SimpleNamespace(
        status_code=status_code,
        is_success=status_code < 400,
        text=json.dumps(payload),
        json=lambda: payload,
        raise_for_status=_raise_for_status,
    )


def _probe_ready() -> SimpleNamespace:
    return _platform_response(
        200, {"status": "ready", "runtime_identity": {"identity_digest": "probe-digest"}}
    )


def _promote_ok(
    commit: str = "candidate-commit", version: int = 7, reload_notified: bool = True
) -> SimpleNamespace:
    return _platform_response(
        200,
        {
            "version": version,
            "commit": commit,
            "artifact_digest": "digest-1",
            "reload_notified": reload_notified,
        },
    )


class PlatformReleaseWorkflowTest(unittest.TestCase):
    """单阶段发版（Phase A）：Platform probe + promote，registry 只读。"""

    def setUp(self) -> None:
        # release_events_v2 有 append-only 触发器不可 DELETE；各用例用独立
        # session_id（release_id 随之唯一），事件断言按 release_id 过滤互不干扰。
        db.execute("DELETE FROM evolve_sessions")

    def _session(self, session_id: str) -> None:
        from app.evolve import db as ev_db
        ev_db.create_session(session_id)
        ev_db.update_session(session_id, status="pending_review")

    @staticmethod
    def _request():
        return SimpleNamespace(state=SimpleNamespace(user_id="developer-1"))

    def _url_patches(self) -> list:
        """把 platform_url / executor_url 指到哨兵地址（断言流量去向）。

        必须通过 release_gate.settings 取实例再 patch：全量跑时其他测试
        （test_harness_api）会 reload app.core.settings 换掉单例，各模块
        绑定的实例可能不一致，直接 patch 本模块 import 到的实例会失效。
        """
        from app.versioning import release_gate
        return [
            patch.object(release_gate.settings, "platform_url", PLATFORM_URL),
            patch.object(release_gate.settings, "executor_url", EXECUTOR_URL),
        ]

    def test_publish_promotes_via_platform(self) -> None:
        """单阶段：冻结 → Platform probe → Platform promote → published。"""
        from app.evolve.api import publish_session

        self._session("platform-publish")
        with ExitStack() as stack:
            for p in self._url_patches():
                stack.enter_context(p)
            http_post = stack.enter_context(
                patch("httpx.post", side_effect=[_probe_ready(), _promote_ok(version=7)])
            )
            stack.enter_context(
                patch("app.versioning.registry_repo.get_version_by_session", return_value=None)
            )
            stack.enter_context(
                patch("app.core.git_ops.commit_candidate", return_value="candidate-commit")
            )
            write_guards = [
                stack.enter_context(patch(f"app.versioning.registry_repo.{name}"))
                for name in (
                    "create_candidate", "promote_candidate", "restore_production",
                    "next_version_number", "get_production_version_number",
                )
            ]
            result = publish_session("platform-publish", self._request())

        self.assertEqual(result["status"], "activated")
        self.assertEqual(result["snapshot_version"], 7)
        self.assertEqual(result["source_commit"], "candidate-commit")

        # 门禁与晋升都打到 platform_url，且零 executor 流量（AC-001）
        self.assertEqual(http_post.call_count, 2)
        urls = [call.args[0] for call in http_post.call_args_list]
        self.assertEqual(urls, [PROBE_URL, PROMOTE_URL])
        for url in urls:
            self.assertNotIn(EXECUTOR_URL, url)
        promote_body = http_post.call_args_list[1].kwargs["json"]
        self.assertEqual(
            promote_body,
            {"source_commit": "candidate-commit",
             "version_note": "进化 session platform-publish 产出的改动"},
        )

        # registry 写线退役：发版路径不再写 registry.json
        for guard in write_guards:
            guard.assert_not_called()

        session = db.query_one(
            "SELECT status FROM evolve_sessions WHERE session_id='platform-publish'"
        )
        self.assertEqual(session["status"], "published")
        events = db.query_all(
            "SELECT status FROM release_events_v2 WHERE release_id='release-platform-publish'"
            " ORDER BY rowid"
        )
        self.assertEqual(
            [e["status"] for e in events],
            ["committed", "registry_promoted", "executor_refresh_ack", "activated"],
        )

    def test_probe_rejected_rejects_publish(self) -> None:
        """Platform probe 返回 rejected → 409 拒绝，不发 promote。"""
        from fastapi import HTTPException
        from app.evolve.api import publish_session

        self._session("probe-rejected")
        with ExitStack() as stack:
            for p in self._url_patches():
                stack.enter_context(p)
            http_post = stack.enter_context(
                patch(
                    "httpx.post",
                    side_effect=[
                        _platform_response(
                            200, {"status": "rejected", "reason": "assembled=False"}
                        )
                    ],
                )
            )
            stack.enter_context(
                patch("app.versioning.registry_repo.get_version_by_session", return_value=None)
            )
            stack.enter_context(
                patch("app.core.git_ops.commit_candidate", return_value="candidate-commit")
            )
            with self.assertRaises(HTTPException) as caught:
                publish_session("probe-rejected", self._request())

        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("assembled=False", str(caught.exception.detail))
        self.assertEqual(http_post.call_count, 1)  # 只有 probe，没有 promote
        session = db.query_one(
            "SELECT status FROM evolve_sessions WHERE session_id='probe-rejected'"
        )
        self.assertEqual(session["status"], "pending_review")

    def test_promote_non_2xx_maps_to_activation_failed(self) -> None:
        """Platform promote 非 2xx → 502 激活失败，session 可重试。"""
        from fastapi import HTTPException
        from app.evolve.api import publish_session

        self._session("promote-fail")
        with ExitStack() as stack:
            for p in self._url_patches():
                stack.enter_context(p)
            stack.enter_context(
                patch("httpx.post", side_effect=[_probe_ready(),
                                                 _platform_response(409, {"detail": "probe 未通过，拒绝晋升"})])
            )
            stack.enter_context(
                patch("app.versioning.registry_repo.get_version_by_session", return_value=None)
            )
            stack.enter_context(
                patch("app.core.git_ops.commit_candidate", return_value="candidate-commit")
            )
            restore = stack.enter_context(
                patch("app.versioning.registry_repo.restore_production")
            )
            with self.assertRaises(HTTPException) as caught:
                publish_session("promote-fail", self._request())

        self.assertEqual(caught.exception.status_code, 502)
        detail = caught.exception.detail
        self.assertEqual(detail["release_status"], "activation_failed")
        self.assertIsNone(detail["executor_restore_error"])
        # Platform promote 原子失败，本地无可回滚物
        restore.assert_not_called()
        session = db.query_one(
            "SELECT status FROM evolve_sessions WHERE session_id='promote-fail'"
        )
        self.assertEqual(session["status"], "pending_review")

    def test_promote_commit_mismatch_fails_publish(self) -> None:
        """PromoteResult.commit 与冻结 commit 不一致 → 报错（一致性断言）。"""
        from fastapi import HTTPException
        from app.evolve.api import publish_session

        self._session("commit-mismatch")
        with ExitStack() as stack:
            for p in self._url_patches():
                stack.enter_context(p)
            stack.enter_context(
                patch("httpx.post", side_effect=[_probe_ready(),
                                                 _promote_ok(commit="other-commit")])
            )
            stack.enter_context(
                patch("app.versioning.registry_repo.get_version_by_session", return_value=None)
            )
            stack.enter_context(
                patch("app.core.git_ops.commit_candidate", return_value="candidate-commit")
            )
            with self.assertRaises(HTTPException) as caught:
                publish_session("commit-mismatch", self._request())

        self.assertEqual(caught.exception.status_code, 500)
        self.assertIn("commit mismatch", str(caught.exception.detail))
        session = db.query_one(
            "SELECT status FROM evolve_sessions WHERE session_id='commit-mismatch'"
        )
        self.assertEqual(session["status"], "pending_review")

    def test_legacy_frozen_candidate_reuses_commit(self) -> None:
        """旧线冻结未晋升的 candidate：复用其 commit，不再重新冻结。"""
        from app.evolve.api import publish_session

        self._session("legacy-frozen")
        legacy_candidate = {
            "version": 9, "commit_hash": "frozen-commit",
            "source_session": "legacy-frozen", "status": "candidate",
        }
        with ExitStack() as stack:
            for p in self._url_patches():
                stack.enter_context(p)
            stack.enter_context(
                patch("httpx.post", side_effect=[_probe_ready(), _promote_ok("frozen-commit", version=12)])
            )
            stack.enter_context(
                patch(
                    "app.versioning.registry_repo.get_version_by_session",
                    return_value=legacy_candidate,
                )
            )
            commit_candidate = stack.enter_context(
                patch("app.core.git_ops.commit_candidate", return_value="should-not-run")
            )
            result = publish_session("legacy-frozen", self._request())

        self.assertEqual(result["status"], "activated")
        self.assertEqual(result["snapshot_version"], 12)
        self.assertEqual(result["source_commit"], "frozen-commit")
        commit_candidate.assert_not_called()

    def test_legacy_activation_failed_retry_completes_event_chain(self) -> None:
        """旧线激活失败（activation_failed）后的重试：事件链只补合法后缀。"""
        from app.evolve.api import publish_session

        self._session("legacy-retry")
        # append-only 表不可清理，事件 id 用随机值避免全量跑时撞 UNIQUE 约束
        db.execute(
            """INSERT INTO release_events_v2
               (release_event_id, release_id, status, candidate_id, actor_user_id, created_at)
               VALUES (?, 'release-legacy-retry', 'activation_failed',
                       'harness-version-9', 'developer-1', '2026-01-01T00:00:00')""",
            (f"evt-old-{uuid4().hex}",),
        )
        legacy_candidate = {
            "version": 9, "commit_hash": "frozen-commit",
            "source_session": "legacy-retry", "status": "candidate",
        }
        with ExitStack() as stack:
            for p in self._url_patches():
                stack.enter_context(p)
            stack.enter_context(
                patch("httpx.post", side_effect=[_probe_ready(), _promote_ok("frozen-commit", version=13)])
            )
            stack.enter_context(
                patch(
                    "app.versioning.registry_repo.get_version_by_session",
                    return_value=legacy_candidate,
                )
            )
            result = publish_session("legacy-retry", self._request())

        self.assertEqual(result["status"], "activated")
        events = db.query_all(
            "SELECT status FROM release_events_v2 WHERE release_id='release-legacy-retry'"
            " ORDER BY rowid"
        )
        # activation_failed → registry_promoted → executor_refresh_ack → activated
        self.assertEqual(
            [e["status"] for e in events],
            ["activation_failed", "registry_promoted", "executor_refresh_ack", "activated"],
        )

    def test_already_published_is_idempotent(self) -> None:
        """legacy registry 已是 production → 幂等返回，不打 Platform。"""
        from app.evolve.api import publish_session

        self._session("idempotent")
        candidate = {
            "version": 11, "commit_hash": "published-commit",
            "source_session": "idempotent", "status": "production",
            "snapshot_trace_id": None,
        }
        with ExitStack() as stack:
            for p in self._url_patches():
                stack.enter_context(p)
            http_post = stack.enter_context(patch("httpx.post"))
            stack.enter_context(
                patch("app.versioning.registry_repo.get_version_by_session", return_value=candidate)
            )
            result = publish_session("idempotent", self._request())

        self.assertEqual(result["status"], "activated")
        self.assertEqual(result["snapshot_version"], 11)
        http_post.assert_not_called()
        session = db.query_one(
            "SELECT status FROM evolve_sessions WHERE session_id='idempotent'"
        )
        self.assertEqual(session["status"], "published")

    def test_manual_rollback_promotes_old_commit_via_platform(self) -> None:
        """rollback：registry 只读查目标 commit → Platform promote 重新晋升。"""
        from app.versioning.snapshot_api import RollbackRequest, rollback_snapshot

        current = {"version": 8, "commit_hash": "current-commit"}
        target = {
            "version": 6, "promotion_status": "retired", "commit_hash": "target-commit",
        }
        with ExitStack() as stack:
            for p in self._url_patches():
                stack.enter_context(p)
            http_post = stack.enter_context(
                patch("httpx.post", side_effect=[_promote_ok("target-commit", version=12)])
            )
            stack.enter_context(
                patch("app.versioning.registry_repo.get_production_version", return_value=current)
            )
            stack.enter_context(
                patch("app.versioning.registry_repo.get_version", return_value=target)
            )
            rollback_write = stack.enter_context(
                patch("app.versioning.registry_repo.rollback")
            )
            stack.enter_context(
                patch("app.versioning.snapshot_api.db.query_one", return_value=None)
            )
            result = rollback_snapshot(
                RollbackRequest(to_version=6, reason="regression"), self._request()
            )

        self.assertEqual(result["status"], "rollback_activated")
        self.assertEqual(result["from_version"], 8)
        self.assertEqual(result["to_version"], 6)
        self.assertEqual(result["source_commit"], "target-commit")
        self.assertEqual(result["platform_version"], 12)

        self.assertEqual(http_post.call_count, 1)
        call = http_post.call_args_list[0]
        self.assertEqual(call.args[0], PROMOTE_URL)
        self.assertEqual(
            call.kwargs["json"],
            {"source_commit": "target-commit", "version_note": "rollback v8 -> v6: regression"},
        )
        # registry 写线退役：不再移动本地 production 指针
        rollback_write.assert_not_called()

    def test_manual_rollback_target_without_commit_rejected(self) -> None:
        """目标版本缺 commit 绑定 → 404（不可执行版本不可回滚）。"""
        from fastapi import HTTPException
        from app.versioning.snapshot_api import RollbackRequest, rollback_snapshot

        current = {"version": 8, "commit_hash": "current-commit"}
        target = {"version": 5, "promotion_status": "retired", "commit_hash": None}
        with ExitStack() as stack:
            for p in self._url_patches():
                stack.enter_context(p)
            http_post = stack.enter_context(patch("httpx.post"))
            stack.enter_context(
                patch("app.versioning.registry_repo.get_production_version", return_value=current)
            )
            stack.enter_context(
                patch("app.versioning.registry_repo.get_version", return_value=target)
            )
            with self.assertRaises(HTTPException) as caught:
                rollback_snapshot(
                    RollbackRequest(to_version=5, reason="bad"), self._request()
                )

        self.assertEqual(caught.exception.status_code, 404)
        self.assertIn("缺少不可变 commit 绑定", str(caught.exception.detail))
        http_post.assert_not_called()


class ReleaseGateDirectTest(unittest.TestCase):
    """release_gate 直测：Platform probe / promote 的调用形状与失败语义。"""

    def setUp(self) -> None:
        # 通过 release_gate.settings 取实例（防其他测试 reload 单例后失配）
        from app.versioning import release_gate
        self._patch = patch.object(release_gate.settings, "platform_url", PLATFORM_URL)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_probe_ready_returns_probe_result(self) -> None:
        from app.versioning.release_gate import probe_candidate

        with patch("httpx.post", return_value=_probe_ready()) as http_post:
            result = probe_candidate("commit-x")

        self.assertIsInstance(result, ProbeResult)
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.runtime_identity, {"identity_digest": "probe-digest"})
        call = http_post.call_args_list[0]
        self.assertEqual(call.args[0], PROBE_URL)
        self.assertEqual(call.kwargs["json"], {"source_commit": "commit-x"})
        self.assertEqual(call.kwargs["timeout"], 150.0)

    def test_probe_rejected_raises_value_error(self) -> None:
        from app.versioning.release_gate import probe_candidate

        response = _platform_response(
            200, {"status": "rejected", "reason": "middleware missing"}
        )
        with patch("httpx.post", return_value=response):
            with self.assertRaises(ValueError) as caught:
                probe_candidate("commit-x")
        self.assertIn("middleware missing", str(caught.exception))

    def test_probe_transport_error_propagates(self) -> None:
        """失败语义与旧版一致：异常向上抛，由 publish_session 兜底捕获。"""
        from app.versioning.release_gate import probe_candidate

        with patch("httpx.post", side_effect=httpx.ConnectError("connection refused")):
            with self.assertRaises(httpx.ConnectError):
                probe_candidate("commit-x")

    def test_promote_returns_promote_result(self) -> None:
        from app.versioning.release_gate import promote_release

        with patch("httpx.post", return_value=_promote_ok("commit-y", version=4)) as http_post:
            result = promote_release("commit-y", version_note="note-1")

        self.assertIsInstance(result, PromoteResult)
        self.assertEqual(result.version, 4)
        self.assertEqual(result.commit, "commit-y")
        call = http_post.call_args_list[0]
        self.assertEqual(call.args[0], PROMOTE_URL)
        self.assertEqual(
            call.kwargs["json"],
            {"source_commit": "commit-y", "version_note": "note-1"},
        )

    def test_promote_non_2xx_raises_release_promote_error(self) -> None:
        from app.versioning.release_gate import ReleasePromoteError, promote_release

        response = _platform_response(409, {"detail": "probe 未通过，拒绝晋升"})
        with patch("httpx.post", return_value=response):
            with self.assertRaises(ReleasePromoteError) as caught:
                promote_release("commit-y")
        self.assertIn("HTTP 409", str(caught.exception))

    def test_promote_transport_error_wrapped(self) -> None:
        from app.versioning.release_gate import ReleasePromoteError, promote_release

        with patch("httpx.post", side_effect=httpx.ConnectError("connection refused")):
            with self.assertRaises(ReleasePromoteError):
                promote_release("commit-y")


if __name__ == "__main__":
    unittest.main()
