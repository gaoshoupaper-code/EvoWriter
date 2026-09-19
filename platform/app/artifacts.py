"""artifact 打包与分发（FR-003 Platform 侧）。

流程：checkout commit → tar.gz（排除 .git / __pycache__ / *.pyc）→
sha256hex digest → 存 {artifact_dir}/{commit}.tar.gz。

幂等：目标文件已存在则直接返回既有元数据（digest 已固化，永不重打）。
打包是确定性的（tar 成员元数据归一 + gzip header 无时间戳，见 _tar_filter
与 build_artifact 内的 GzipFile 包装）：被保留策略清掉的包按 commit 重打，
digest 与账本既有记录严格一致，「commit ↔ digest 唯一」不变量成立。

清理：打包成功后按文件修改时间保留最近 N 个（settings.artifact_retention，
LRU 语义；被清理的包在下次被请求时可按 commit 重打，digest 内容一致）。
"""
from __future__ import annotations

import gzip
import logging
import os
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from contracts.platform import ArtifactMeta, SurfaceFingerprint

from app.agent.git_checkout import checkout_commit, cleanup_checkout
from app.core.settings import get_settings, resolve_path
from app.surface_scan import scan_surface, sha256_file

logger = logging.getLogger("platform.artifacts")

# tar 内不携带的内容：git 元数据与字节码缓存不属于装配面
_EXCLUDED_PARTS = frozenset({".git", "__pycache__"})


@dataclass
class BuiltArtifact:
    """一次打包（或幂等命中）的产物：artifact 元数据 + 顺手扫出的指纹。

    打包和指纹扫描消费的是同一个 checkout——一次 clone 两用，省一半 git IO。
    include_fingerprint=False 时 fingerprint 为 None（调用方只消费 meta，
    如 meta 端点惰性打包；省掉一次全目录扫描）。
    """

    meta: ArtifactMeta
    fingerprint: SurfaceFingerprint | None


def artifact_path(commit: str) -> Path:
    """commit 对应的 tar.gz 路径（不管是否存在）。"""
    return resolve_path(get_settings().artifact_dir) / f"{commit}.tar.gz"


def _sidecar_path(commit: str) -> Path:
    """打包时固化的 meta sidecar 路径（与 tar.gz 同目录同名不同后缀）。"""
    return resolve_path(get_settings().artifact_dir) / f"{commit}.meta.json"


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """tar 成员过滤器：排除 .git / __pycache__ / *.pyc，并归一元数据。

    归一（mtime/uid/gid/uname 置零）是确定性打包的一半：成员元数据随打包
    机器与时刻漂移，不归一则同 commit 重打 digest 必变，破坏「commit ↔
    digest 唯一」的账本不变量（另一半是 gzip header 的 mtime=0）。
    """
    parts = Path(info.name).parts
    if any(part in _EXCLUDED_PARTS for part in parts):
        return None
    if info.name.endswith(".pyc"):
        return None
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def _meta_from_existing(path: Path, commit: str) -> ArtifactMeta:
    """从既有 tar.gz 文件还原元数据（幂等命中路径）。"""
    with tarfile.open(path, "r:gz") as tar:
        file_count = sum(
            1 for m in tar.getmembers() if m.isfile()
        )
    built_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    return ArtifactMeta(
        commit=commit,
        digest=sha256_file(path),
        size_bytes=path.stat().st_size,
        file_count=file_count,
        built_at=built_at,
    )


def build_artifact(
    commit: str,
    include_fingerprint: bool = True,
    *,
    source_dir: Path | None = None,
) -> BuiltArtifact:
    """打包指定 commit 的 harness 包（幂等）。

    已有 tar.gz → 不重打，返回既有元数据 + 现场重扫指纹（指纹是纯函数，
    现场重扫与打包时扫描结果一致；避免再为指纹单独 clone 一次）。
    include_fingerprint=False 只跳过指纹计算，不跳过 checkout 打包。
    source_dir 传入时复用调用方已有的干净 checkout（如 promote 复用 probe
    的 checkout，省一次 clone）；此时清理责任在调用方，本函数不删目录。
    """
    artifact_dir = resolve_path(get_settings().artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    target = artifact_path(commit)

    if target.exists():
        fingerprint = _fingerprint_only(commit) if include_fingerprint else None
        return BuiltArtifact(meta=_meta_from_existing(target, commit), fingerprint=fingerprint)

    checkout = source_dir if source_dir is not None else checkout_commit(commit)
    try:
        fingerprint = scan_surface(checkout) if include_fingerprint else None
        # 先写临时文件再原子替换：打包中断不会留下半截 tar.gz 被 digest 固化
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{commit}.", suffix=".tar.gz.tmp", dir=str(artifact_dir)
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        file_count = 0
        try:
            # 确定性 gzip：filename=""（不嵌原始临时文件名）、mtime=0（不嵌打包
            # 时刻）——tarfile.open("w:gz") 的便捷写法会把当前时间写进 gzip
            # header，同 commit 重打 digest 必漂移，故显式包 GzipFile。
            with open(tmp_path, "wb") as raw, gzip.GzipFile(
                filename="", mode="wb", fileobj=raw, mtime=0,
            ) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
                file_count = _add_tree(tar, checkout)
            os.replace(tmp_path, target)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        meta = ArtifactMeta(
            commit=commit,
            digest=sha256_file(target),
            size_bytes=target.stat().st_size,
            file_count=file_count,
            built_at=datetime.now(timezone.utc).isoformat(),
        )
        # sidecar 固化元数据：find_meta 后续读取 O(1)，无需解 tar / 全量读盘
        _sidecar_path(commit).write_text(meta.model_dump_json(), encoding="utf-8")
        logger.info("artifact 已打包: %s digest=%s", target.name, meta.digest[:12])
        _apply_retention(artifact_dir)
        return BuiltArtifact(meta=meta, fingerprint=fingerprint)
    finally:
        if source_dir is None:
            cleanup_checkout(checkout)


def _add_tree(tar: tarfile.TarFile, checkout: Path) -> int:
    """整棵目录进 tar（arcname 为相对路径），返回收录的普通文件数。"""
    file_count = 0
    for path in sorted(checkout.rglob("*")):
        rel = path.relative_to(checkout).as_posix()
        if any(part in _EXCLUDED_PARTS for part in path.relative_to(checkout).parts):
            continue
        if path.suffix == ".pyc":
            continue
        if path.is_file():
            file_count += 1
        tar.add(path, arcname=rel, recursive=False, filter=_tar_filter)
    return file_count


def _fingerprint_only(commit: str) -> SurfaceFingerprint:
    """只为指纹做一次 checkout（幂等命中打包缓存时用）。"""
    checkout = checkout_commit(commit)
    try:
        return scan_surface(checkout)
    finally:
        cleanup_checkout(checkout)


def _apply_retention(artifact_dir: Path) -> None:
    """保留最近 N 个 tar.gz（按 mtime，新→旧），删掉更老的。"""
    retention = get_settings().artifact_retention
    if retention <= 0:
        return
    archives = sorted(
        (p for p in artifact_dir.glob("*.tar.gz") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in archives[retention:]:
        stale.unlink(missing_ok=True)
        # sidecar 随 tar.gz 一同清理，不留孤儿元数据
        _sidecar_path(stale.name.removesuffix(".tar.gz")).unlink(missing_ok=True)
        logger.info("artifact 保留策略清理: %s", stale.name)


def find_meta(commit: str) -> ArtifactMeta | None:
    """已打包则返回元数据，未打包返回 None（不触发打包）。

    优先读打包时固化的 sidecar（O(1)）；无 sidecar 的既有包走旧路径
    （解 tar 数 file_count + 全量读盘算 digest），行为等价。
    """
    target = artifact_path(commit)
    if not target.exists():
        return None
    sidecar = _sidecar_path(commit)
    if sidecar.exists():
        try:
            return ArtifactMeta.model_validate_json(sidecar.read_text(encoding="utf-8"))
        except ValueError:
            # sidecar 损坏（半写/手改）：回退旧路径从 tar.gz 还原，不放大故障
            logger.warning("meta sidecar 不合法，回退全量读取: %s", sidecar.name)
    return _meta_from_existing(target, commit)


__all__ = ["BuiltArtifact", "artifact_path", "build_artifact", "find_meta"]
