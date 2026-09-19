"""版本账本（SQLite 单文件，REQ-20260919-202344 第 3 章数据模型）。

四张表：
- manifests：harness_commit → surface 指纹 + artifact 摘要。指纹不可变（DEC-001）；
  artifact_digest 允许从 NULL 补填一次（绑定自动建 manifest 时不打包，promote 时补）。
- bindings：trace_id ↔ manifest 的 Run 绑定。trace_id 是幂等去重键（fail-static
  补账依赖：同 trace_id 重复签发返回既有记录，绝不覆盖）。
- versions：发版流水（version 单调递增，promote 时追加）。
- meta：production 指针（version/commit/promoted_at）。

并发口径：低流量控制面，采用「每次操作短连接 + 写事务 BEGIN IMMEDIATE」。
BEGIN IMMEDIATE 在写入前拿写锁，避免 promote 并发时读旧 max(version) 互相覆盖。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from contracts.platform import (
    BindingRecord,
    LlmConfigSnapshot,
    ManifestRecord,
    SurfaceFingerprint,
)

# meta 表的 production 指针键
KEY_PRODUCTION_VERSION = "production_version"
KEY_PRODUCTION_COMMIT = "production_commit"
KEY_PRODUCTION_PROMOTED_AT = "production_promoted_at"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS manifests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    harness_commit TEXT NOT NULL UNIQUE,
    artifact_digest TEXT,
    surface_fp_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'production',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bindings (
    trace_id TEXT PRIMARY KEY,
    manifest_id INTEGER NOT NULL,
    harness_commit TEXT NOT NULL,
    llm_json TEXT,
    run_purpose TEXT NOT NULL DEFAULT 'production',
    degraded INTEGER NOT NULL DEFAULT 0,
    runtime_identity_digest TEXT,
    bound_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER NOT NULL UNIQUE,
    "commit" TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utcnow_iso() -> str:
    """UTC ISO 8601 时间戳（账本所有 created_at/bound_at 用同一格式）。"""
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ProductionPointer:
    """meta 表中的 production 指针视图。"""

    version: int
    commit: str
    promoted_at: str


class Ledger:
    """账本读写门面。每次操作独立短连接，不持有长连接（线程安全）。"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    # ── 连接与建表 ─────────────────────────────────────────────

    @contextmanager
    def connect(self):
        """短连接。isolation_level=None = 自动提交，写事务由调用方显式 BEGIN。"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = None
        try:
            yield conn
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        # 建库目录只在此处做一次；connect 每次操作都跑，不该重复 mkdir
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(_SCHEMA)

    # ── versions ───────────────────────────────────────────────

    def versions_count(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM versions").fetchone()
        return int(row["n"])

    def list_versions(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                'SELECT version, "commit", note, created_at FROM versions'
                " ORDER BY version"
            ).fetchall()
        return [dict(r) for r in rows]

    def max_version(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS v FROM versions"
            ).fetchone()
        return int(row["v"])

    def has_version_commit(self, commit: str) -> bool:
        """versions 流水里是否存在该 commit（FR-001 导入的版本无 manifest，靠此判定）。"""
        with self.connect() as conn:
            row = conn.execute(
                'SELECT 1 FROM versions WHERE "commit" = ? LIMIT 1', (commit,)
            ).fetchone()
        return row is not None

    # ── meta / production 指针 ─────────────────────────────────

    def get_production(self) -> ProductionPointer | None:
        with self.connect() as conn:
            rows = {
                r["key"]: r["value"]
                for r in conn.execute(
                    "SELECT key, value FROM meta WHERE key LIKE 'production_%'"
                ).fetchall()
            }
        if KEY_PRODUCTION_VERSION not in rows or KEY_PRODUCTION_COMMIT not in rows:
            return None
        return ProductionPointer(
            version=int(rows[KEY_PRODUCTION_VERSION]),
            commit=rows[KEY_PRODUCTION_COMMIT],
            promoted_at=rows.get(KEY_PRODUCTION_PROMOTED_AT, ""),
        )

    # ── manifests ──────────────────────────────────────────────

    def get_manifest_by_commit(self, harness_commit: str) -> ManifestRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM manifests WHERE harness_commit = ?", (harness_commit,)
            ).fetchone()
        return _row_to_manifest(row) if row else None

    def create_manifest(
        self, harness_commit: str, fingerprint: SurfaceFingerprint
    ) -> ManifestRecord:
        """按 commit 建 manifest（算指纹、不打包）。

        commit 已存在则直接返回既有记录（指纹不可变，不覆盖）——
        绑定自动建 manifest 与 promote 的 get-or-create 共用此语义。
        """
        fp_json = fingerprint.model_dump_json()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO manifests (harness_commit, surface_fp_json,"
                    " status, created_at) VALUES (?, ?, 'candidate', ?)"
                    " ON CONFLICT(harness_commit) DO NOTHING",
                    (harness_commit, fp_json, utcnow_iso()),
                )
                row = conn.execute(
                    "SELECT * FROM manifests WHERE harness_commit = ?",
                    (harness_commit,),
                ).fetchone()
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return _row_to_manifest(row)

    def fill_artifact_digest(self, harness_commit: str, digest: str) -> None:
        """补填 artifact 摘要。只允许从 NULL 补一次，不覆盖既有值。"""
        with self.connect() as conn:
            conn.execute(
                "UPDATE manifests SET artifact_digest = ?"
                " WHERE harness_commit = ? AND artifact_digest IS NULL",
                (digest, harness_commit),
            )

    # ── bindings ───────────────────────────────────────────────

    def create_binding(
        self,
        *,
        trace_id: str,
        manifest_id: int,
        harness_commit: str,
        llm_config: LlmConfigSnapshot | None,
        run_purpose: str,
        degraded: bool,
        runtime_identity_digest: str | None,
    ) -> tuple[BindingRecord, bool]:
        """插入绑定。trace_id 冲突 → 返回既有记录 + created=False（幂等铁律）。"""
        llm_json = llm_config.model_dump_json() if llm_config else None
        bound_at = utcnow_iso()
        created = False
        with self.connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO bindings (trace_id, manifest_id, harness_commit,"
                    " llm_json, run_purpose, degraded, runtime_identity_digest,"
                    " bound_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')",
                    (
                        trace_id, manifest_id, harness_commit, llm_json,
                        run_purpose, int(degraded), runtime_identity_digest, bound_at,
                    ),
                )
                created = True
            except sqlite3.IntegrityError:
                # 并发同 trace_id：既有记录胜出，不覆盖任何字段
                pass
            # 同一连接读回，不再新开连接（INSERT 已自动提交，读得到）
            row = conn.execute(
                "SELECT * FROM bindings WHERE trace_id = ?", (trace_id,)
            ).fetchone()
        return _row_to_binding(row), created

    def get_binding(self, trace_id: str) -> BindingRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM bindings WHERE trace_id = ?", (trace_id,)
            ).fetchone()
        return _row_to_binding(row) if row else None

    # ── FR-001 初始导入 ─────────────────────────────────────────

    def import_registry(self, registry: dict) -> int:
        """把 bare repo registry.json 的 versions + production 指针导入账本。

        FR-001 幂等：账本 versions 非空则跳过（在写锁内复查，防双启动竞态）。
        任一条目缺 commit_hash / production 指针不合法 → RuntimeError，
        事务回滚不留半导入状态（启动失败报错）。

        Returns:
            实际导入的 version 条数（跳过时 0）。
        """
        entries = registry.get("versions") or []
        if not entries:
            return 0

        validated: list[tuple[int, str, str, str]] = []
        for entry in entries:
            version = entry.get("version")
            commit = entry.get("commit_hash")
            # 历史迁移期允许缺 commit 的版本混在 registry（现实仓库发生过），
            # 但 Platform 账本是 commit 不可变绑定的真源，导入即校验
            if not isinstance(version, int) or not isinstance(commit, str) or not commit:
                raise RuntimeError(
                    f"registry.json version 条目缺少合法 version/commit_hash: {entry}"
                )
            created_at = entry.get("created_at")
            if not isinstance(created_at, str) or not created_at:
                created_at = utcnow_iso()
            validated.append(
                (version, commit, str(entry.get("change_summary") or ""), created_at)
            )

        production = registry.get("production")
        if not isinstance(production, int):
            raise RuntimeError("registry.json 缺少 production 指针")
        prod_commit = next((c for v, c, _, _ in validated if v == production), None)
        if prod_commit is None:
            raise RuntimeError(
                f"registry.json production v{production} 不在 versions 中或缺少 commit 绑定"
            )

        imported = 0
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT COUNT(*) AS n FROM versions").fetchone()
                if row["n"] > 0:
                    # 账本非空：不覆盖既有数据（幂等铁律）
                    conn.execute("COMMIT")
                    return 0
                conn.executemany(
                    'INSERT INTO versions (version, "commit", note, created_at)'
                    " VALUES (?, ?, ?, ?)",
                    validated,
                )
                for key, value in (
                    (KEY_PRODUCTION_VERSION, str(production)),
                    (KEY_PRODUCTION_COMMIT, prod_commit),
                    (KEY_PRODUCTION_PROMOTED_AT, utcnow_iso()),
                ):
                    conn.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                        (key, value),
                    )
                conn.execute("COMMIT")
                imported = len(validated)
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return imported

    # ── promote 原子晋升 ────────────────────────────────────────

    def apply_promotion(
        self,
        *,
        commit: str,
        note: str,
        artifact_digest: str,
        fingerprint: SurfaceFingerprint,
    ) -> int:
        """单事务完成晋升：versions 追加 + 旧 production 标 superseded +
        新 manifest 建档/转 production + meta 指针切换。

        version = max+1 在写锁内计算，防并发 promote 拿到相同版本号。

        Returns:
            本次晋升的 version 号。
        """
        fp_json = fingerprint.model_dump_json()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM versions"
                ).fetchone()
                version = int(row["v"])
                conn.execute(
                    'INSERT INTO versions (version, "commit", note, created_at)'
                    " VALUES (?, ?, ?, ?)",
                    (version, commit, note, utcnow_iso()),
                )
                conn.execute(
                    "UPDATE manifests SET status = 'superseded' WHERE status = 'production'"
                )
                # 已有 manifest（如绑定自动建档）只补 digest 与状态，指纹不动（不可变）
                conn.execute(
                    "INSERT INTO manifests (harness_commit, artifact_digest,"
                    " surface_fp_json, status, created_at) VALUES (?, ?, ?, 'production', ?)"
                    " ON CONFLICT(harness_commit) DO UPDATE SET"
                    " artifact_digest = excluded.artifact_digest, status = 'production'",
                    (commit, artifact_digest, fp_json, utcnow_iso()),
                )
                for key, value in (
                    (KEY_PRODUCTION_VERSION, str(version)),
                    (KEY_PRODUCTION_COMMIT, commit),
                    (KEY_PRODUCTION_PROMOTED_AT, utcnow_iso()),
                ):
                    conn.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                        (key, value),
                    )
                conn.execute("COMMIT")
                return version
            except Exception:
                conn.execute("ROLLBACK")
                raise


def _row_to_binding(row: sqlite3.Row) -> BindingRecord:
    """bindings 行 → BindingRecord（llm 快照从 JSON 反序列化）。"""
    llm = (
        LlmConfigSnapshot.model_validate_json(row["llm_json"])
        if row["llm_json"]
        else None
    )
    return BindingRecord(
        trace_id=row["trace_id"],
        manifest_id=row["manifest_id"],
        harness_commit=row["harness_commit"],
        llm_config=llm,
        run_purpose=row["run_purpose"],
        degraded=bool(row["degraded"]),
        runtime_identity_digest=row["runtime_identity_digest"],
        bound_at=row["bound_at"],
        status=row["status"],
    )


def _row_to_manifest(row: sqlite3.Row) -> ManifestRecord:
    """manifests 行 → ManifestRecord（surface 指纹从 JSON 反序列化）。"""
    return ManifestRecord(
        manifest_id=row["id"],
        harness_commit=row["harness_commit"],
        artifact_digest=row["artifact_digest"],
        surface_fingerprint=SurfaceFingerprint.model_validate_json(
            row["surface_fp_json"]
        ),
        status=row["status"],
        created_at=row["created_at"],
    )


def ledger_from_settings():
    """按当前 settings 构建账本实例（每次新建短连接，无共享状态）。"""
    from app.core.settings import get_settings, resolve_path

    return Ledger(resolve_path(get_settings().db_path))


__all__ = [
    "Ledger",
    "ProductionPointer",
    "ledger_from_settings",
    "utcnow_iso",
    "KEY_PRODUCTION_VERSION",
    "KEY_PRODUCTION_COMMIT",
    "KEY_PRODUCTION_PROMOTED_AT",
]
