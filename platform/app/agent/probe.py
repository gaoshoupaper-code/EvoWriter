"""发版门禁 probe（语义迁移自 executor /internal/harness/probe 的 probe_harness）。

门禁装配链（与迁移源一致的语义）：
1. bare repo 干净 checkout 指定 commit（clone 新目录，不带任何工作区残留）
2. 校验 middleware/artifact_snapshot.py 存在（trace 取证是 executor 不可回退项）
3. 子进程内 importlib 唯一模块名加载候选包 + FakeListChatModel 真实装配
4. 装配后复查 checkout 不 dirty（assemble 期间写文件 = 包有副作用，拒）

子进程隔离（见 probe_worker.py 模块 docstring）：生产 harness 包是薄包装，
assemble 链 import executor 的 app.platform.*，与 platform 服务自身的顶层
app 包撞名，无法同进程共存。子进程以 executor 目录为 app 唯一来源，装配
崩溃/内存污染不影响主进程——这与 Runtime 侧 A/B 的 worker_process 隔离是
同一决策谱系（DEC-007 门禁不寄生生产 Runtime）。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from contracts.platform import ProbeResult

from app.agent.git_checkout import checkout_commit, cleanup_checkout
from app.core.settings import get_settings, project_root, resolve_path

logger = logging.getLogger("platform.probe")

# probe worker 的硬超时：装配含 import + 图构建，正常秒级；卡死视为包有病。
_PROBE_TIMEOUT_SECONDS = 120


def _worker_env() -> dict[str, str]:
    """子进程 PYTHONPATH：仓库根（contracts 可导）+ executor 目录（app 可导）。"""
    settings = get_settings()
    executor_dir = resolve_path(settings.executor_code_path)
    if not (executor_dir / "app" / "__init__.py").is_file():
        raise RuntimeError(
            f"executor 代码目录不可用: {executor_dir}（probe 无法装配薄包装 harness，"
            f"检查 PLATFORM_EXECUTOR_CODE_PATH）"
        )
    # Candidate imports receive runtime settings, never service credentials.
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT"}}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(project_root()), str(executor_dir))
        if p
    )
    return env


def probe_candidate(
    source_commit: str, *, keep_checkout: bool = False
) -> ProbeResult | tuple[ProbeResult, Path | None]:
    """候选 commit 门禁装配。任何失败都收敛为 rejected + reason（不 500）。

    keep_checkout=True（promote 复用路径）：返回 (ProbeResult, checkout_path)，
    ready 时 checkout 交给调用方打包并负责 cleanup_checkout；失败路径
    返回 (ProbeResult, None)，checkout 已在内部照常清理。默认 False 时
    返回裸 ProbeResult，行为与历史一致。
    """
    checkout = None
    try:
        checkout = checkout_commit(source_commit)
        worker = Path(__file__).resolve().parent / "probe_worker.py"
        # Cleanup belongs to the trusted parent: the sandbox cannot remove its
        # workspace directory from an otherwise protected parent directory.
        with tempfile.TemporaryDirectory(prefix="platform_probe_workspace_") as workspace, tempfile.TemporaryFile() as verdict:
            result = subprocess.run(
                [sys.executable, str(worker), str(checkout), source_commit, workspace, str(verdict.fileno())],
                capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SECONDS,
                env=_worker_env(), pass_fds=(verdict.fileno(),),
            )
            verdict.seek(0)
            raw_verdict = verdict.read(16385)
        if result.returncode != 0 or not raw_verdict or len(raw_verdict) > 16384:
            rejected = ProbeResult(
                status="rejected",
                reason=f"probe worker 异常退出 rc={result.returncode}: "
                       f"{result.stderr.strip()[-500:]}",
            )
            return (rejected, None) if keep_checkout else rejected
        # Candidate stdout is a log stream and cannot certify assembly success.
        payload = json.loads(raw_verdict)
        if payload.get("status") == "ready":
            actual = subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout, capture_output=True, text=True, timeout=30)
            dirty = subprocess.run(["git", "status", "--porcelain"], cwd=checkout, capture_output=True, text=True, timeout=30)
            if (actual.returncode or not actual.stdout.strip().startswith(source_commit)
                    or dirty.returncode or dirty.stdout.strip()
                    or not (checkout / "middleware" / "artifact_snapshot.py").is_file()
                    or payload.get("checked_commit") != source_commit):
                raise ValueError("装配完成凭据与父进程核对的源码状态不一致")
            logger.info("probe 通过: commit=%s", source_commit)
            ready = ProbeResult(status="ready", runtime_identity={"harness_commit": source_commit,
                "harness_dirty": False, "artifact_snapshot_middleware": True})
            if keep_checkout:
                handed_off, checkout = checkout, None  # 交给调用方，finally 不清理
                return ready, handed_off
            return ready
        logger.warning("probe 拒绝: commit=%s reason=%s", source_commit, payload.get("reason"))
        rejected = ProbeResult(status="rejected", reason=payload.get("reason"))
        return (rejected, None) if keep_checkout else rejected
    except subprocess.TimeoutExpired:
        logger.warning("probe 超时: commit=%s", source_commit)
        rejected = ProbeResult(status="rejected", reason=f"probe 超时（>{_PROBE_TIMEOUT_SECONDS}s）")
        return (rejected, None) if keep_checkout else rejected
    except Exception as exc:  # noqa: BLE001 — 门禁语义：一切异常 = rejected
        logger.warning("probe 拒绝: commit=%s err=%s", source_commit, exc)
        rejected = ProbeResult(status="rejected", reason=f"{type(exc).__name__}: {exc}")
        return (rejected, None) if keep_checkout else rejected
    finally:
        if checkout is not None:
            cleanup_checkout(checkout)


__all__ = ["probe_candidate"]
