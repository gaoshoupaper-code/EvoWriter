"""manifest 服务：harness_commit → ManifestRecord 的惰性建档。

统一三个入口的 get-or-create 语义：
- POST /api/bindings 遇到账本没有的 commit（A/B 候选 Run）→ 自动建档（算指纹，不打包）
- GET /api/production 读生产状态时 → 生产 commit 若无 manifest（FR-001 只导
  versions/meta，不扫指纹）→ 惰性补指纹，保证 ProductionStatus 的指纹字段真实
- promote → 建档后由晋升事务转 production 并补 digest
"""
from __future__ import annotations

import logging

from contracts.platform import ManifestRecord

from app.core.ledger import Ledger
from app.surface_scan import fingerprint_commit

logger = logging.getLogger("platform.manifests")


def ensure_manifest(ledger: Ledger, commit: str) -> ManifestRecord:
    """manifest 不存在则 checkout+扫指纹建档；存在直接返回（指纹不可变）。

    Raises:
        RuntimeError: commit 不在 bare repo（checkout_commit 抛出）。
    """
    existing = ledger.get_manifest_by_commit(commit)
    if existing is not None:
        return existing
    fingerprint = fingerprint_commit(commit)
    record = ledger.create_manifest(commit, fingerprint)
    logger.info("manifest 自动建档: commit=%s (%d 个指纹文件)", commit[:12],
                sum(len(d) for d in (fingerprint.a_text, fingerprint.b_param, fingerprint.c_code)))
    return record


__all__ = ["ensure_manifest"]
