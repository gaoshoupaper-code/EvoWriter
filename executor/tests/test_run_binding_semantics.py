"""Run 绑定语义测试（REQ-20260919-202344 Phase A，AC-005/AC-007 executor 侧）。

覆盖两条硬验收：
- AC-005 Run 中配置不漂移：build_writer_model 优先用 Run 绑定快照——
  快照有值时，llm_config loader（治理源）的值变化不影响本 Run 的模型构建。
- AC-007 resume 兼容门禁：decision=incompatible → Run 收敛为 incompatible_resume
  终态（cancel_run reason）+ 非活跃态先重建再收敛；非 awaiting 的 run 不动。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from contracts.platform import LlmConfigSnapshot, ResumeCheck

SNAP_MODEL = "gpt-4o-snap"
SNAP_BASE = "http://snapshot.example/v1"


def _settings(**over):
    base = dict(
        writer_model="fallback-model",
        writer_temperature=None,
        writer_top_p=None,
        platform_api_key="",
        platform_base_url="",
        openai_api_key="",
        openai_base_url="",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _snap() -> LlmConfigSnapshot:
    return LlmConfigSnapshot(
        model=SNAP_MODEL, base_url=SNAP_BASE, api_key_ref=None, source="evolution",
    )


def _make_service() -> MagicMock:
    """最小 MetaAgentService 壳（只需 trace_recorder 与待测方法）。"""
    from app.domains.writing.agent import MetaAgentService

    svc = MetaAgentService.__new__(MetaAgentService)
    svc.trace_recorder = MagicMock()
    return svc


def _make_recorder_shell():
    """真 cancel_awaiting_run + mock 记账原语的 recorder 壳。

    守卫语义（查 awaiting、重建活跃态、cancel）在 TraceRecorder 真方法里，
    壳只 mock 它依赖的原语（_queues / find_run_by_trace_id / _rebuild_active_state
    / cancel_run）。
    """
    from app.platform.trace.recorder import TraceRecorder

    recorder = TraceRecorder.__new__(TraceRecorder)
    recorder._queues = {}
    recorder.find_run_by_trace_id = MagicMock(return_value=None)
    recorder._rebuild_active_state = MagicMock()
    recorder.cancel_run = MagicMock()
    return recorder


class TestRunLlmLockAc005:
    """AC-005：Run 级 LLM 配置锁定（快照优先于治理源现值）。"""

    def test_snapshot_wins_over_loader(self):
        """快照有值 → model/base_url 用快照；loader 返回不同值也不生效。"""
        from app.domains.writing.models import build_writer_model

        loader_cfg = SimpleNamespace(
            model="drifted-model", base_url="http://drift/v1", api_key="k",
        )
        with patch(
            "app.platform.llm_config.loader.get_active_llm_config",
            return_value=loader_cfg,
        ):
            model = build_writer_model(_settings(), llm_override=_snap())
        assert model.model_name == SNAP_MODEL
        assert model.openai_api_base == SNAP_BASE

    def test_snapshot_is_redacted(self):
        """快照脱敏红线：序列化后无明文 key 字段（contracts 契约测试之外的
        服务侧再验一次——构建快照的入口也不引入 key）。"""
        svc = _make_service()
        cfg = SimpleNamespace(
            model="m", base_url="http://x/v1", api_key="SECRET",
        )
        with patch(
            "app.platform.llm_config.loader.get_active_llm_config", return_value=cfg,
        ):
            snap = svc._build_llm_snapshot()
        assert snap is not None
        assert "api_key" not in snap.model_dump()
        assert "SECRET" not in snap.model_dump_json()


class TestResumeGateAc007:
    """AC-007：resume 门禁拒绝后的终态收敛语义。"""

    def test_reject_resume_delegates_to_cancel_awaiting_run(self):
        """incompatible → _reject_resume 调 recorder.cancel_awaiting_run
        （reason=incompatible_resume）；守卫/重建逻辑封装在 recorder 公共方法内。"""
        svc = _make_service()
        recorder = svc.trace_recorder
        thread = SimpleNamespace(thread_id="t1")

        svc._reject_resume(thread, "trace-1", "c_code changed")

        recorder.cancel_awaiting_run.assert_called_once_with(
            thread, "trace-1", reason="incompatible_resume",
        )

    def test_cancel_awaiting_run_rebuilds_then_cancels(self):
        """内存丢失（_queues 无此 trace）+ index awaiting_input →
        先重建最小活跃态再收敛 cancel_run(reason=incompatible_resume)。"""
        recorder = _make_recorder_shell()
        thread = SimpleNamespace(thread_id="t1")
        run = SimpleNamespace(status="awaiting_input")
        recorder.find_run_by_trace_id = MagicMock(return_value=run)

        recorder.cancel_awaiting_run(thread, "trace-1", reason="incompatible_resume")

        recorder._rebuild_active_state.assert_called_once_with(thread, run)
        recorder.cancel_run.assert_called_once_with(
            thread, "trace-1", reason="incompatible_resume",
        )

    def test_cancel_awaiting_run_skips_non_awaiting_run(self):
        """run 不存在或非 awaiting → 不动账面（门禁已拒绝续跑，账面清理
        只对等待中的 run 有意义）。"""
        recorder = _make_recorder_shell()

        # index 里查无此 run
        recorder.cancel_awaiting_run(SimpleNamespace(), "trace-2", reason="incompatible_resume")
        recorder.cancel_run.assert_not_called()

        # run 存在但已非 awaiting
        recorder.find_run_by_trace_id = MagicMock(
            return_value=SimpleNamespace(status="running")
        )
        recorder.cancel_awaiting_run(SimpleNamespace(), "trace-3", reason="incompatible_resume")
        recorder.cancel_run.assert_not_called()

    def test_gate_resume_delegates_to_binding_client(self):
        """_gate_resume 透传 binding_client.resume_check 的判定结果。"""
        svc = _make_service()
        check = ResumeCheck(
            trace_id="trace-3", decision="incompatible", reason="b_param changed",
            bound_commit="a", current_commit="b", changed_layers=["b_param"],
        )
        fake_client = MagicMock()
        fake_client.resume_check.return_value = check
        with patch(
            "app.platform.agent.binding_client.get_binding_client",
            return_value=fake_client,
        ):
            assert svc._gate_resume("trace-3") is check


class TestResumeSnapshotAntiPoison:
    """resume 回读快照的 base_url 防投毒校验（LLM 凭据红线，review #4）。

    快照不含 key、key 走受控通道现取——若快照 base_url 与受控配置不一致
    （或受控配置为 None），真实 key 会被发往快照指定的端点。必须弃用快照。
    """

    def _svc_with_binding(self, snapshot):
        svc = _make_service()
        record = SimpleNamespace(trace_id="t", llm_config=snapshot)
        fake_client = MagicMock()
        fake_client.get_binding.return_value = record
        return svc, fake_client

    def test_mismatched_base_url_drops_snapshot(self):
        """快照 base_url ≠ 受控配置 → 返回 None（退回 loader 链）。"""
        svc, fake_client = self._svc_with_binding(_snap())
        controlled = SimpleNamespace(
            model="m", base_url="http://controlled.example/v1", api_key="k",
        )
        with patch(
            "app.platform.agent.binding_client.get_binding_client",
            return_value=fake_client,
        ), patch(
            "app.platform.llm_config.loader.get_active_llm_config",
            return_value=controlled,
        ):
            assert svc._resume_binding_snapshot("t") is None

    def test_matching_base_url_returns_snapshot(self):
        """快照 base_url 与受控配置一致 → 返回快照（版本一致续跑生效）。"""
        snap = _snap()
        svc, fake_client = self._svc_with_binding(snap)
        controlled = SimpleNamespace(
            model="drifted-model", base_url=SNAP_BASE, api_key="k",
        )
        with patch(
            "app.platform.agent.binding_client.get_binding_client",
            return_value=fake_client,
        ), patch(
            "app.platform.llm_config.loader.get_active_llm_config",
            return_value=controlled,
        ):
            assert svc._resume_binding_snapshot("t") is snap

    def test_no_controlled_config_drops_snapshot(self):
        """受控配置为 None（未配置 LLM）→ 同样弃用快照，不信任其 base_url。"""
        svc, fake_client = self._svc_with_binding(_snap())
        with patch(
            "app.platform.agent.binding_client.get_binding_client",
            return_value=fake_client,
        ), patch(
            "app.platform.llm_config.loader.get_active_llm_config",
            return_value=None,
        ):
            assert svc._resume_binding_snapshot("t") is None

    def test_no_binding_returns_none(self):
        """无绑定记录（legacy Run）→ None（既有语义不变）。"""
        svc = _make_service()
        fake_client = MagicMock()
        fake_client.get_binding.return_value = None
        with patch(
            "app.platform.agent.binding_client.get_binding_client",
            return_value=fake_client,
        ):
            assert svc._resume_binding_snapshot("t") is None
