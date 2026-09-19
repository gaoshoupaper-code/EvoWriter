"""Run 绑定客户端 —— executor 向 Platform 签发/查询 Run 绑定（FR-004，Phase A）。

职责：
  - bind：Run 开始时向 Platform POST 绑定（trace_id ↔ harness commit + LLM 快照）。
    fail-static（DEC-008）：Platform 不可达时不抛异常、不阻塞生成——签发一条
    degraded 本地记录，把完整 BindingCreate JSON 落 spool，后台 30s 重放补账
    （Platform 端按 trace_id 幂等，补账不覆盖既有字段）。
  - resume_check：HITL 恢复前的兼容门禁（FR-005）。Platform 不可达时保守返回
    incompatible（判定过程失败按不兼容处理，需求风险处置）。
  - 成功绑定后把 {commit, llm 快照(脱敏), 绑定信息} 写 known_production.json，
    作为本地 fail-static 缓存（与 llm_config_loader 的本地降级缓存同思路）。

安全红线：LlmConfigSnapshot 本身不含明文 key（contracts 红线），spool/known_
production 落盘的都是脱敏形状。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from contracts.platform import (
    BindingAck,
    BindingCreate,
    BindingRecord,
    LlmConfigSnapshot,
    ResumeCheck,
)

logger = logging.getLogger("writer.binding_client")

_HTTP_TIMEOUT = 5.0          # 绑定是 Run 启动路径上的同步调用，必须快速失败
_REPLAY_INTERVAL = 30.0      # spool 补账重放周期（退避起点与成功复位值）
# 连续失败轮的退避序列：30s → 1m → 2m → 4m → 封顶 5min
_REPLAY_BACKOFF_SEQUENCE = (60.0, 120.0, 240.0)
_REPLAY_BACKOFF_CAP = 300.0
_SPOOL_MAX_FILES = 1000      # spool 文件数上限（超出删最老，防磁盘无限涨）


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _resolve_executor_path(p: str) -> Path:
    """相对路径基于 executor 根解析（与 data/trace_outcomes.db 同口径）。"""
    path = Path(p)
    if not path.is_absolute():
        # 本文件在 executor/app/platform/agent/，上三级是 executor/
        path = Path(__file__).resolve().parents[3] / path
    return path


def _atomic_write_json(path: Path, payload: dict) -> None:
    """原子写 JSON：临时文件 + rename，防进程中途被杀留下半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp-{path.name}")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    os.replace(tmp, path)


class BindingClient:
    """Platform Run 绑定客户端（同步 HTTP + 后台 spool 重放协程）。"""

    def __init__(
        self,
        platform_url: str,
        spool_dir: Path,
        known_production_path: Path,
        *,
        client=None,
        spool_max_files: int = _SPOOL_MAX_FILES,
    ) -> None:
        self._platform_url = platform_url.rstrip("/")
        self._spool_dir = Path(spool_dir)
        self._spool_dir.mkdir(parents=True, exist_ok=True)
        self._known_production_path = Path(known_production_path)
        self._spool_max_files = spool_max_files
        # 测试注入 httpx.Client（MockTransport）；生产惰性创建
        self._client = client
        self._replay_task: asyncio.Task | None = None

    def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.Client(
                base_url=self._platform_url, timeout=_HTTP_TIMEOUT,
            )
        return self._client

    # ── 绑定签发（fail-static，绝不抛异常）────────────────────

    def bind(
        self,
        trace_id: str,
        harness_commit: str,
        llm_snapshot: LlmConfigSnapshot | None,
        run_purpose: str = "production",
        runtime_identity_digest: str | None = None,
    ) -> tuple[BindingRecord, bool]:
        """签发 Run 绑定，返回 (记录, created)。

        成功：POST /api/bindings，并更新本地 known_production.json。
        失败（网络/非 2xx）：返回 degraded=True 的本地记录（不抛异常），
        完整 BindingCreate JSON 落 spool 等后台补账。
        """
        req = BindingCreate(
            trace_id=trace_id,
            harness_commit=harness_commit,
            llm_config=llm_snapshot,
            run_purpose=run_purpose,
            degraded=False,
            runtime_identity_digest=runtime_identity_digest,
        )
        try:
            resp = self._http().post("/api/bindings", json=req.model_dump(mode="json"))
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code} {resp.text[:200]}")
            ack = BindingAck.model_validate(resp.json())
        except Exception as exc:
            logger.warning(
                "Run 绑定签发失败（fail-static 降级 + spool 补账）: trace=%s err=%s",
                trace_id, exc,
            )
            self._spool(trace_id, req)
            # manifest_id=0：本地降级记录无 Platform manifest 可关联；
            # 补账幂等键是 trace_id，Platform 端不会用这条本地记录覆盖正式记录。
            return BindingRecord(
                trace_id=trace_id,
                manifest_id=0,
                harness_commit=harness_commit,
                llm_config=llm_snapshot,
                run_purpose=run_purpose,
                degraded=True,
                runtime_identity_digest=runtime_identity_digest,
                bound_at=_now_iso(),
                status="active",
            ), False
        self._write_known_production(ack.binding)
        if ack.created:
            logger.info(
                "Run 绑定已签发: trace=%s commit=%s",
                ack.binding.trace_id, ack.binding.harness_commit,
            )
        return ack.binding, ack.created

    def _spool(self, trace_id: str, req: BindingCreate) -> None:
        """绑定请求落盘（原子写），等 replay_spooled 补账。失败只记日志。

        degraded 置 True 再落盘：补账是 fail-static 窗口内的降级补签，
        Platform 账本必须记录降级事实（AC-006），不能当作在线正常签发。
        """
        spooled = req.model_copy(update={"degraded": True})
        try:
            _atomic_write_json(
                self._spool_dir / f"{trace_id}.json", spooled.model_dump(mode="json"),
            )
        except OSError:
            logger.exception("绑定 spool 写入失败: trace=%s", trace_id)
            return
        self._enforce_spool_cap()

    def _enforce_spool_cap(self) -> None:
        """spool 文件数上限：超出删最老并 warning。

        Platform 长时间不可达时 spool 才会堆积到上限——被删的最老补账会丢
        （该 Run 的降级绑定不再补签），换来磁盘不无限增长。
        """
        if self._spool_max_files <= 0:
            return
        try:
            files = sorted(
                self._spool_dir.glob("*.json"), key=lambda p: p.stat().st_mtime,
            )
            excess = len(files) - self._spool_max_files
            for stale in files[:excess] if excess > 0 else ():
                stale.unlink(missing_ok=True)
                logger.warning(
                    "spool 超出上限 %d，删除最老补账: %s（该 Run 降级绑定不再补签）",
                    self._spool_max_files, stale.name,
                )
        except OSError:
            logger.debug("spool 上限清理失败", exc_info=True)

    def _write_known_production(self, record: BindingRecord) -> None:
        """把最近一次成功绑定对应的生产版本摘要写本地缓存。失败只记日志。"""
        payload = {
            "commit": record.harness_commit,
            # 脱敏视图：LlmConfigSnapshot.redacted 不含明文 key（contracts 红线）
            "llm_config": record.llm_config.redacted() if record.llm_config else None,
            "manifest_id": record.manifest_id,
            "bound_at": record.bound_at,
            "run_purpose": record.run_purpose,
        }
        try:
            _atomic_write_json(self._known_production_path, payload)
        except OSError:
            logger.exception("known_production.json 写入失败")

    def get_known_production(self) -> dict | None:
        """读本地 known_production 缓存（冷启动 fail-static 的回退源，FR-003）。

        Returns: 最近一次成功绑定时写入的 {commit, llm_config, ...} 摘要；
        文件缺失/损坏返回 None（不抛异常——回退链路自身也必须 fail-static）。
        """
        try:
            return json.loads(self._known_production_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # ── 查询 ──────────────────────────────────────────────────

    def resume_check(self, trace_id: str) -> ResumeCheck | None:
        """resume 兼容门禁查询。

        Returns:
            ResumeCheck（decision=compatible/incompatible）；无绑定记录（404，
            含迁移前 legacy Run）返回 None；Platform 不可达返回保守 incompatible
            （判定过程自身失败按不兼容处理）。
        """
        try:
            resp = self._http().get(f"/api/bindings/{trace_id}/resume-check")
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            return ResumeCheck.model_validate(resp.json())
        except Exception as exc:
            logger.warning(
                "resume 门禁查询失败，保守拒绝恢复: trace=%s err=%s", trace_id, exc,
            )
            return ResumeCheck(
                trace_id=trace_id,
                decision="incompatible",
                reason="platform 不可达,保守拒绝恢复",
                bound_commit="",
                current_commit="",
            )

    def get_binding(self, trace_id: str) -> BindingRecord | None:
        """查绑定记录（resume 后取绑定 LLM 快照用）。失败/无记录返回 None。"""
        try:
            resp = self._http().get(f"/api/bindings/{trace_id}")
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            return BindingRecord.model_validate(resp.json())
        except Exception as exc:
            logger.warning("绑定查询失败: trace=%s err=%s", trace_id, exc)
            return None

    # ── spool 补账重放 ────────────────────────────────────────

    def replay_spooled(self) -> int:
        """扫描 spool 目录逐个重发 POST（Platform 按 trace_id 幂等）。成功删文件。

        Returns: 本轮补账成功的条数。
        """
        replayed = 0
        for path in sorted(self._spool_dir.glob("*.json")):
            try:
                req = BindingCreate.model_validate(json.loads(path.read_text("utf-8")))
            except (OSError, ValueError) as exc:
                logger.warning("spool 文件损坏，跳过: %s err=%s", path.name, exc)
                continue
            try:
                resp = self._http().post("/api/bindings", json=req.model_dump(mode="json"))
                if resp.status_code < 400:
                    path.unlink(missing_ok=True)
                    replayed += 1
                    logger.info("绑定补账成功: trace=%s", req.trace_id)
            except Exception as exc:
                logger.debug("绑定补账失败（下轮重试）: %s err=%s", path.name, exc)
        return replayed

    async def _replay_loop(self) -> None:
        """补账循环：连续失败轮按 30s→1m→2m→4m 退避（封顶 5min）。

        任一轮全成功（spool 清空）即复位 30s——Platform 恢复后尽快清账；
        持续失败时退避拉长，减少对不可达 Platform 的无效轰炸。
        """
        interval = _REPLAY_INTERVAL
        backoff_index = 0  # 连续失败轮计数（成功复位 0）
        while True:
            await asyncio.sleep(interval)
            try:
                def _replay_round() -> int:
                    # HTTP 是同步调用，放线程池避免阻塞事件循环
                    self.replay_spooled()
                    return len(list(self._spool_dir.glob("*.json")))

                remaining = await asyncio.to_thread(_replay_round)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 —— 重放异常不拖垮后台任务
                logger.debug("绑定补账重放异常（继续下一轮）", exc_info=True)
                remaining = 1  # 异常轮按失败处理，走退避
            if remaining == 0:
                backoff_index = 0
                interval = _REPLAY_INTERVAL
            elif backoff_index < len(_REPLAY_BACKOFF_SEQUENCE):
                interval = _REPLAY_BACKOFF_SEQUENCE[backoff_index]
                backoff_index += 1
            else:
                interval = _REPLAY_BACKOFF_CAP

    def start_replay_loop(self) -> None:
        """启动补账后台协程（lifespan 调用，幂等）。须在事件循环线程内调用。"""
        if self._replay_task is None or self._replay_task.done():
            self._replay_task = asyncio.create_task(self._replay_loop())
            logger.info("绑定补账重放已启动（间隔 %.0fs）", _REPLAY_INTERVAL)

    async def aclose_replay_loop(self) -> None:
        """关闭补账协程（lifespan shutdown 调用）。"""
        if self._replay_task is not None:
            self._replay_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._replay_task
            self._replay_task = None


# ── 模块级单例 ──

_client: BindingClient | None = None


def get_binding_client() -> BindingClient:
    """全局 BindingClient 单例（首次调用时从 settings 初始化）。"""
    global _client
    if _client is None:
        from app.platform.core.settings import get_settings

        s = get_settings()
        _client = BindingClient(
            s.platform_url,
            _resolve_executor_path(s.binding_spool_dir),
            _resolve_executor_path(s.known_production_path),
        )
    return _client


def reset_binding_client() -> None:
    """丢弃单例（测试改 env 后重建用）。"""
    global _client
    _client = None


__all__ = ["BindingClient", "get_binding_client", "reset_binding_client"]
