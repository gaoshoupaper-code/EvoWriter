"""artifact_snapshot —— 产物快照中间件（翻译领域薄包装）。

薄包装 re-export：实现归 executor 平台层（PlatformArtifactCaptureMiddleware），
领域包只保留本文件占位 + 重导出。门禁（probe_worker）要求每个 harness 包
自带 middleware/artifact_snapshot.py——trace 取证是 executor 不可回退项，
领域在这里声明「取证能力由平台提供」，不自带第二份实现。
"""
from __future__ import annotations

from app.platform.agent.middleware.artifact_capture import (
    EvidenceCaptureError,
    PlatformArtifactCaptureMiddleware,
)

# 领域内沿用写作领域的类名口径（ArtifactSnapshotMiddleware），指向平台实现
ArtifactSnapshotMiddleware = PlatformArtifactCaptureMiddleware

__all__ = [
    "ArtifactSnapshotMiddleware",
    "EvidenceCaptureError",
    "PlatformArtifactCaptureMiddleware",
]
