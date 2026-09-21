"""Writer Trace V2 的追加型质量事实与发版状态机。

血缘边（lineage_edges）与评估/卷宗完整性闸门随休眠评估系统裁撤
（REQ-20260921-124733 DEC-002/DEC-006：表保留，代码删除）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import app.core.db as db
from app.core.settings import settings
from contracts.trace.payload import ContentAddressedPayloadStore


_OUTCOME_TYPES = {
    "copy", "regenerate", "adopt", "edit_diff", "human_rating",
    "candidate_improved", "published", "rolled_back",
}
_RELEASE_TRANSITIONS: dict[str | None, set[str]] = {
    None: {"committed"},
    "committed": {"registry_promoted"},
    "registry_promoted": {"executor_refresh_ack", "activation_failed"},
    "executor_refresh_ack": {"activated", "activation_failed"},
    "activated": {"rollback_activated"},
    "activation_failed": {"registry_promoted", "rollback_activated"},
    "rollback_activated": set(),
}


def append_outcome(
    *,
    target_type: str,
    target_id: str,
    outcome_type: str,
    actor_user_id: str | None,
    payload: Any | None = None,
    outcome_id: str | None = None,
) -> str:
    if target_type not in {"trace", "artifact_revision"}:
        raise ValueError("outcome target must be trace or artifact_revision")
    if outcome_type not in _OUTCOME_TYPES:
        raise ValueError("unsupported outcome_type")
    stable_id = outcome_id or f"outcome-{uuid4().hex}"
    payload_id = _store_payload(payload) if payload is not None else None
    existing = db.query_one("SELECT * FROM outcome_records WHERE outcome_id=?", (stable_id,))
    expected = (target_type, target_id, outcome_type, payload_id, actor_user_id)
    if existing is not None:
        actual = tuple(
            existing.get(key)
            for key in ("target_type", "target_id", "outcome_type", "payload_id", "actor_user_id")
        )
        if actual != expected:
            raise ValueError(f"outcome_id conflict: {stable_id}")
        return stable_id
    db.execute(
        """INSERT INTO outcome_records
           (outcome_id, target_type, target_id, outcome_type, payload_id,
            actor_user_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (*((stable_id,) + expected), _now()),
    )
    return stable_id


def latest_release_status(release_id: str) -> str | None:
    """查该 release 最新事件的状态（无事件返回 None）。

    事件链兼容判定共用：append_release_event 的迁移校验与 evolve api 的
    「按最新状态只补发合法后缀」都基于同一查询。
    """
    prior = db.query_one(
        """SELECT status FROM release_events_v2 WHERE release_id=?
           ORDER BY created_at DESC, rowid DESC LIMIT 1""",
        (release_id,),
    )
    return prior.get("status") if prior else None


def append_release_event(
    *,
    release_id: str,
    status: str,
    candidate_id: str | None,
    actor_user_id: str | None,
    release_event_id: str | None = None,
) -> str:
    previous_status = latest_release_status(release_id)
    if status not in _RELEASE_TRANSITIONS.get(previous_status, set()):
        raise ValueError(f"invalid release transition: {previous_status} -> {status}")
    stable_id = release_event_id or f"release-event-{uuid4().hex}"
    db.execute(
        """INSERT INTO release_events_v2
           (release_event_id, release_id, status, candidate_id, actor_user_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (stable_id, release_id, status, candidate_id, actor_user_id, _now()),
    )
    return stable_id


def _store_payload(value: Any) -> str:
    ref = ContentAddressedPayloadStore(settings.trace_payload_path).put(value)
    db.execute(
        """INSERT INTO payload_objects
           (payload_id, content_hash, kind, size_bytes, sensitivity, expires_at,
            storage_path, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(payload_id) DO UPDATE SET expires_at=excluded.expires_at""",
        (
            ref.payload_id, ref.content_hash, ref.kind, ref.size_bytes, ref.sensitivity,
            ref.expires_at, str(settings.trace_payload_path / f"{ref.payload_id}.json"), _now(),
        ),
    )
    return ref.payload_id


def _now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = ["append_outcome", "append_release_event", "latest_release_status"]
