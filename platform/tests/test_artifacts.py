"""artifact 打包与分发测试：打包存在 + digest 匹配；幂等；meta 端点；download 字节。"""
from __future__ import annotations

import hashlib
import tarfile

from app.artifacts import artifact_path, build_artifact, find_meta
from app.core.ledger import Ledger


def _sha256_file(path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def test_build_creates_archive_with_matching_digest(platform_env, harness_git):
    built = build_artifact(harness_git.head)

    archive = artifact_path(harness_git.head)
    assert archive.exists()
    assert built.meta.commit == harness_git.head
    assert built.meta.digest == _sha256_file(archive)  # digest = tar.gz 文件 sha256hex
    assert built.meta.size_bytes == archive.stat().st_size
    # head = 首个包 commit（registry.json 在其后的 commit 才进库）：
    # __init__.py / middleware/artifact_snapshot.py / prompts/x.md /
    # skills/s/SKILL.md / .gitignore = 5 个文件
    assert built.meta.file_count == 5
    assert built.meta.built_at

    # 指纹顺带产出：三类文件齐备
    fp = built.fingerprint
    assert "prompts/x.md" in fp.a_text
    assert "skills/s/SKILL.md" in fp.a_text
    assert "middleware/artifact_snapshot.py" in fp.c_code

    # tar 内不带 .git / __pycache__
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    assert not any(".git" in name.split("/") for name in names)
    assert not any("__pycache__" in name.split("/") for name in names)


def test_build_idempotent_skips_rebuild(platform_env, harness_git):
    first = build_artifact(harness_git.head)
    archive = artifact_path(harness_git.head)
    mtime_after_first = archive.stat().st_mtime_ns

    second = build_artifact(harness_git.head)

    # 二次调用不重打：digest 一致、文件未被重写（mtime 不变）
    assert second.meta.digest == first.meta.digest
    assert archive.stat().st_mtime_ns == mtime_after_first


def test_rebuild_after_delete_is_byte_identical(platform_env, harness_git):
    """确定性打包（review #5）：打包 → 删 → 重打 → digest 严格一致。

    tar 成员元数据（mtime/uid/gid/uname）归一 + gzip header 无时间戳；
    同 commit 重打字节级一致，「commit ↔ digest 唯一」不变量跨保留策略清理
    成立（被清掉的包重打回原 digest）。
    """
    import time

    first = build_artifact(harness_git.head)
    archive = artifact_path(harness_git.head)
    first_bytes = archive.read_bytes()

    # 删除后隔 2s 重打：若 mtime/时间戳未归一，digest 必漂移
    archive.unlink()
    time.sleep(2.0)
    second = build_artifact(harness_git.head)

    assert second.meta.digest == first.meta.digest
    assert artifact_path(harness_git.head).read_bytes() == first_bytes

    # tar 成员元数据确已归一（mtime/uid/gid=0，uname/gname 空）
    with tarfile.open(artifact_path(harness_git.head), "r:gz") as tar:
        members = tar.getmembers()
    assert members
    for m in members:
        assert m.mtime == 0
        assert m.uid == 0 and m.gid == 0
        assert m.uname == "" and m.gname == ""


def test_meta_endpoint_lazy_build_and_404(client, platform_env, harness_git):
    # 未知 commit（无 tar 无 manifest 且不在 versions）→ 404
    resp = client.get("/api/artifacts/0000000000000000000000000000000000000000/meta")
    assert resp.status_code == 404

    # 绑定触发自动建 manifest（不打包）→ meta 端点惰性打包返回元数据
    client.post(
        "/api/bindings",
        json={"trace_id": "t-meta", "harness_commit": harness_git.head},
    )
    resp = client.get(f"/api/artifacts/{harness_git.head}/meta")
    assert resp.status_code == 200
    body = resp.json()
    assert body["commit"] == harness_git.head
    assert len(body["digest"]) == 64
    assert body["size_bytes"] > 0
    assert body["file_count"] == 5

    # 惰性打包后回填 manifest 的 artifact_digest
    ledger = Ledger(platform_env.db)
    manifest = ledger.get_manifest_by_commit(harness_git.head)
    assert manifest.artifact_digest == body["digest"]
    assert artifact_path(harness_git.head).exists()

    # 已打包路径：find_meta 直接命中（不再走 manifest 判定）
    assert find_meta(harness_git.head).digest == body["digest"]


def test_meta_endpoint_accepts_registry_imported_version(client, platform_env, harness_git):
    """FR-001 导入的历史版本（versions 有、manifests 无）→ meta 200。

    导入只写 versions/production 不建 manifest——meta 端点必须先补建
    manifest（checkout + 算指纹）再惰性打包，A/B 存量线才能拉到 meta。
    """
    ledger = Ledger(platform_env.db)
    assert ledger.get_manifest_by_commit(harness_git.head) is None  # 导入不建 manifest
    assert ledger.has_version_commit(harness_git.head)  # 但 versions 认识它

    resp = client.get(f"/api/artifacts/{harness_git.head}/meta")
    assert resp.status_code == 200
    body = resp.json()
    assert body["commit"] == harness_git.head
    assert len(body["digest"]) == 64
    assert artifact_path(harness_git.head).exists()

    # 补建的 manifest：指纹真实 + digest 已回填
    manifest = ledger.get_manifest_by_commit(harness_git.head)
    assert manifest is not None
    assert manifest.surface_fingerprint.c_code  # 真指纹非空（c_code 含 middleware）
    assert manifest.artifact_digest == body["digest"]


def test_download_returns_tar_gz_bytes(client, platform_env, harness_git):
    build_artifact(harness_git.head)
    resp = client.get(f"/api/artifacts/{harness_git.head}/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/gzip")
    assert bytes(resp.content) == artifact_path(harness_git.head).read_bytes()

    # 未打包 commit → 404
    resp = client.get("/api/artifacts/1111111111111111111111111111111111111111/download")
    assert resp.status_code == 404


def test_retention_keeps_recent_n(platform_env, harness_git, monkeypatch):
    """保留策略：只留最近 N 个包（LRU 按 mtime），更老的清理。"""
    from app.core.settings import get_settings

    monkeypatch.setenv("PLATFORM_ARTIFACT_RETENTION", "2")
    get_settings.cache_clear()

    rev = {"n": 0}

    def mutate(work):
        rev["n"] += 1
        (work / "prompts" / "x.md").write_text(
            f"# prompt rev {rev['n']}\n", encoding="utf-8"
        )

    # 造 3 个 commit，按时间顺序分别打包；保留 2 → 最老的第一个包被清理
    commits = [harness_git.head]
    for i in range(2):
        commits.append(harness_git.commit(f"rev {i + 1}", mutate=mutate))
    for commit in commits:
        build_artifact(commit)

    remaining = {p.name.removesuffix(".tar.gz") for p in platform_env.artifacts.glob("*.tar.gz")}
    assert remaining == set(commits[1:])
