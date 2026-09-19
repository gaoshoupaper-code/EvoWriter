"""artifact 客户端 —— 从 Platform 控制面下载 harness artifact（Phase A，DEC-012）。

替代旧 git_sync 的 bare repo pull/clone 链路：生产包与 A/B 候选包统一改为
「GET meta → GET tar.gz → sha256 校验 → 安全解包到缓存目录」。

为什么必须校验 digest：artifact 是装配面的唯一真源。损坏/被篡改的包一旦装配，
Run 绑定记录的 commit 与实际执行的代码就会不一致，事后无法追溯——digest 不符
宁可 RuntimeError 拒绝装配，也不带病运行。

为什么本地 LRU 缓存：生产与 A/B 共用一份缓存，同 commit 只下载一次；
保留最近 N 个 commit 目录，防止长跑进程磁盘无限增长（A/B 线切换频繁时）。

下载/元数据失败一律 RuntimeError，是否降级由调用方决定（loader/路由层语义不同）。
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tarfile
import tempfile
import threading
import uuid
from pathlib import Path, PurePosixPath

from contracts.platform import ArtifactMeta, ProductionStatus

logger = logging.getLogger("writer.artifact_client")

# 本地缓存保留的 commit 目录数（LRU，按目录 mtime 排序淘汰）
_CACHE_RETENTION = 5
_META_TIMEOUT = 10.0       # meta 是小 JSON，快速失败
_DOWNLOAD_TIMEOUT = 300.0  # tar.gz 可达数十 MB，给足传输时间
# 解包落位时固化的 digest sidecar 文件名（缓存命中比对用，review #6）
_DIGEST_SIDECAR = ".digest"


def _resolve_project_path(p: str) -> Path:
    """相对路径基于项目根 Writer/ 解析（与旧 harness clone 目录同口径）。"""
    path = Path(p)
    if not path.is_absolute():
        # 本文件在 executor/app/platform/agent/，上四级是项目根 Writer/
        path = Path(__file__).resolve().parents[4] / path
    return path


def _validate_member(member: tarfile.TarInfo) -> None:
    """拒绝绝对路径 / .. 穿越 / 链接成员（防 artifact 写穿缓存目录）。

    Platform 打包源是受信任的 bare repo，此处校验是纵深防御：artifact 经由
    HTTP 传输，任何中间层损坏都不该获得任意写盘能力。链接成员（symlink/hardlink）
    在解包侧没有安全的跨平台实现（Windows 无特权建链接），直接拒绝——
    harness 包是纯文本 + Python 源码，不含链接。
    """
    name = member.name
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or name.startswith("\\"):
        raise RuntimeError(f"artifact 拒绝不安全路径成员: {name}")
    if member.issym() or member.islnk():
        raise RuntimeError(f"artifact 拒绝链接成员: {name}")


class ArtifactClient:
    """Platform artifact 下载器（同步 HTTP，进程内复用连接）。"""

    # per-commit 进程内互斥锁（review #11）：并发冷启动（首 Run + 对账协程 +
    # A/B 线程）同时未命中缓存时，无锁会互删 staging / 重复下载。类级共享，
    # 锁粒度 = commit，跨实例生效。
    _commit_locks: dict[str, threading.Lock] = {}
    _commit_locks_guard = threading.Lock()

    @classmethod
    def _commit_lock(cls, commit: str) -> threading.Lock:
        with cls._commit_locks_guard:
            lock = cls._commit_locks.get(commit)
            if lock is None:
                lock = threading.Lock()
                cls._commit_locks[commit] = lock
            return lock

    def __init__(self, platform_url: str, cache_dir: Path, *, client=None) -> None:
        self._platform_url = platform_url.rstrip("/")
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        # 测试注入 httpx.Client（MockTransport）；生产惰性创建
        self._client = client

    def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.Client(
                base_url=self._platform_url, timeout=_META_TIMEOUT,
            )
        return self._client

    # ── 生产状态 ──────────────────────────────────────────────

    def current_production(self) -> ProductionStatus:
        """GET /api/production：当前生产版本（冷启动对账源）。

        Raises:
            RuntimeError: Platform 不可达 / 无生产版本（404）/ 响应非法。
        """
        try:
            resp = self._http().get("/api/production")
        except Exception as exc:
            raise RuntimeError(f"查询 Platform 生产版本失败: {exc}") from exc
        if resp.status_code != 200:
            raise RuntimeError(
                f"查询 Platform 生产版本失败: HTTP {resp.status_code} {resp.text[:200]}"
            )
        try:
            return ProductionStatus.model_validate(resp.json())
        except Exception as exc:
            raise RuntimeError(f"Platform 生产版本响应非法: {exc}") from exc

    # ── artifact 下载与缓存 ───────────────────────────────────

    def ensure_artifact(self, commit: str, *, expected_digest: str | None = None) -> Path:
        """确保 commit 的 artifact 已解包在本地缓存，返回解包目录。

        缓存命中（目录存在且含 __init__.py）直接返回，不发任何 HTTP——但
        调用方带 expected_digest 时先与落位时固化的 .digest 比对，不符删
        缓存重下（命中路径不能短路 digest 校验）。expected_digest（reload
        通知携带）与 Platform meta.digest 不符时拒绝——通知与账本不一致
        说明发版链路出了问题，不能装配。

        同 commit 并发未命中由 per-commit 锁互斥（下载+解包+落位全程）。

        Raises:
            RuntimeError: 下载失败 / digest 不符 / 包内成员不安全 / 缺 __init__.py。
                          失败时清理半成品，缓存目录不留脏数据。
        """
        dest = self._cache_dir / commit
        if self._cache_hit(dest, expected_digest):
            return dest
        with self._commit_lock(commit):
            # 拿到锁后复查：等锁期间可能已被并发调用完成落位
            if self._cache_hit(dest, expected_digest):
                return dest
            meta = self._fetch_meta(commit)
            if expected_digest is not None and expected_digest != meta.digest:
                raise RuntimeError(
                    f"artifact 摘要不符,拒绝装配: notice={expected_digest} meta={meta.digest}"
                )
            archive = self._download(commit, meta)
            try:
                self._extract(commit, archive, dest, digest=meta.digest)
            finally:
                archive.unlink(missing_ok=True)
            self._evict_old_commits()
            return dest

    def _cache_hit(self, dest: Path, expected_digest: str | None) -> bool:
        """缓存命中判定：目录在即命中；带 expected_digest 时过 .digest 比对。

        比对不符 → 删缓存返回 False（走重下）；.digest sidecar 缺失（外部
        预置的旧缓存）无从比对，保持命中——下载路径的 meta 校验是主门禁。
        """
        if not (dest / "__init__.py").is_file():
            return False
        if expected_digest is None:
            return True
        recorded = self._read_digest_sidecar(dest)
        if recorded is None or recorded == expected_digest:
            return True
        logger.warning(
            "artifact 缓存 digest 与期望不符，删除重下: %s cached=%s expect=%s",
            dest.name, recorded, expected_digest,
        )
        shutil.rmtree(dest, ignore_errors=True)
        return False

    @staticmethod
    def _read_digest_sidecar(dest: Path) -> str | None:
        try:
            return (dest / _DIGEST_SIDECAR).read_text(encoding="utf-8").strip()
        except OSError:
            return None

    def _fetch_meta(self, commit: str) -> ArtifactMeta:
        try:
            resp = self._http().get(f"/api/artifacts/{commit}/meta")
        except Exception as exc:
            raise RuntimeError(f"拉取 artifact 元数据失败: {commit} {exc}") from exc
        if resp.status_code != 200:
            raise RuntimeError(
                f"拉取 artifact 元数据失败: {commit} HTTP {resp.status_code}"
            )
        try:
            return ArtifactMeta.model_validate(resp.json())
        except Exception as exc:
            raise RuntimeError(f"artifact 元数据响应非法: {commit} {exc}") from exc

    def _download(self, commit: str, meta: ArtifactMeta) -> Path:
        """流式下载 tar.gz 到缓存目录临时文件，边下边算 sha256。

        Returns: 已通过 digest 校验的临时文件路径。

        Raises:
            RuntimeError: 下载失败或摘要不符（不符时删半成品）。
        """
        import httpx

        # mkstemp 返回的 fd 必须立刻关掉：Windows 上未关闭句柄会让后续
        # unlink/unlink(missing_ok=True) 抛 PermissionError（文件被占用）
        fd, tmp_name = tempfile.mkstemp(prefix=f".dl-{commit}-", dir=self._cache_dir)
        os.close(fd)
        tmp = Path(tmp_name)
        digest = hashlib.sha256()
        try:
            with self._http().stream(
                "GET", f"/api/artifacts/{commit}/download", timeout=_DOWNLOAD_TIMEOUT,
            ) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"下载 artifact 失败: {commit} HTTP {resp.status_code}"
                    )
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                        digest.update(chunk)
                        fh.write(chunk)
            if digest.hexdigest() != meta.digest:
                raise RuntimeError(
                    f"artifact 摘要不符,拒绝装配: expect={meta.digest} actual={digest.hexdigest()}"
                )
            return tmp
        except httpx.HTTPError as exc:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"下载 artifact 失败: {commit} {exc}") from exc
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def _extract(
        self, commit: str, archive: Path, dest: Path, *, digest: str | None = None
    ) -> None:
        """安全解包：先解到临时目录，成功后原子改名到 dest。

        解到临时目录再 rename，保证 dest 目录要么完整存在、要么不存在——
        并发 ensure_artifact 或进程中途被杀都不会留下「半解包但目录在」的假缓存
        （假缓存会被缓存命中检查放过，装配出残缺包）。staging 名带 uuid4：
        同进程内并发解包同 commit 时互不踩踏（锁之外的纵深防御）。
        digest 非空时把下载期校验过的摘要固化成 .digest sidecar——后续缓存
        命中比对用（review #6）。
        """
        staging = self._cache_dir / f".extract-{commit}-{uuid.uuid4().hex}"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        try:
            with tarfile.open(archive, "r:gz") as tf:
                for member in tf.getmembers():
                    _validate_member(member)
                    target = staging / Path(*PurePosixPath(member.name).parts)
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        src = tf.extractfile(member)
                        if src is None:
                            raise RuntimeError(f"artifact 成员不可读: {member.name}")
                        with open(target, "wb") as fh:
                            shutil.copyfileobj(src, fh)
                    else:
                        raise RuntimeError(
                            f"artifact 含不支持的成员类型: {member.name} ({member.type})"
                        )
            if not (staging / "__init__.py").is_file():
                raise RuntimeError(f"artifact 缺少包入口 __init__.py: {commit}")
            if digest is not None:
                (staging / _DIGEST_SIDECAR).write_text(digest, encoding="utf-8")
            shutil.rmtree(dest, ignore_errors=True)
            staging.rename(dest)
            logger.info("artifact 已装配: commit=%s → %s", commit, dest)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _evict_old_commits(self) -> None:
        """LRU 清理：按目录 mtime 保留最近 N 个 commit 目录。

        在每次新下载后执行（缓存命中的路径不清理，避免高频 stat 开销）。
        下载临时文件（.dl-* 前缀）的残骸一并扫掉（.extract-* 由 _extract 的
        finally 自行清理，不在此处理）。
        """
        try:
            entries = [d for d in self._cache_dir.iterdir() if d.is_dir()]
            # mtime 降序 = 最近使用的在前；越界部分淘汰
            entries.sort(key=lambda d: d.stat().st_mtime, reverse=True)
            for stale in entries[_CACHE_RETENTION:]:
                shutil.rmtree(stale, ignore_errors=True)
                logger.info("artifact 缓存清理: %s", stale.name)
            # 临时目录残骸（下载/解包中断遗留）无条件清掉
            for leftover in self._cache_dir.glob(".dl-*"):
                leftover.unlink(missing_ok=True)
        except OSError:
            logger.debug("artifact 缓存清理失败", exc_info=True)


# ── 模块级单例 ──

_client: ArtifactClient | None = None


def get_artifact_client() -> ArtifactClient:
    """全局 ArtifactClient 单例（首次调用时从 settings 初始化）。"""
    global _client
    if _client is None:
        from app.platform.core.settings import get_settings

        s = get_settings()
        _client = ArtifactClient(s.platform_url, _resolve_project_path(s.artifact_cache_dir))
    return _client


def reset_artifact_client() -> None:
    """丢弃单例（测试改 env 后重建用）。"""
    global _client
    _client = None


__all__ = ["ArtifactClient", "get_artifact_client", "reset_artifact_client"]
