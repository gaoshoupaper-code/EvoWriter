"""Run 绑定测试：创建+查询；幂等(created=False 且字段不被覆盖)；未知 commit 自动建 manifest。"""
from __future__ import annotations

from app.artifacts import artifact_path
from app.core.ledger import Ledger


def test_create_and_get_binding(client, harness_git):
    resp = client.post(
        "/api/bindings",
        json={
            "trace_id": "trace-1",
            "harness_commit": harness_git.head,
            "llm_config": {
                "model": "test-model",
                "base_url": "http://llm-test",
                "api_key_ref": "cfg-3",
            },
            "run_purpose": "optimization",
            "runtime_identity_digest": "rid-1",
        },
    )
    assert resp.status_code == 200
    ack = resp.json()
    assert ack["created"] is True
    binding = ack["binding"]
    assert binding["trace_id"] == "trace-1"
    assert binding["harness_commit"] == harness_git.head
    assert binding["manifest_id"] > 0
    assert binding["run_purpose"] == "optimization"
    assert binding["degraded"] is False
    assert binding["status"] == "active"
    assert binding["bound_at"]
    # llm 快照脱敏存储：只有引用，无明文 key
    assert binding["llm_config"]["api_key_ref"] == "cfg-3"
    assert "api_key" not in binding["llm_config"]

    got = client.get("/api/bindings/trace-1")
    assert got.status_code == 200
    assert got.json() == binding


def test_create_binding_defaults(client, harness_git):
    resp = client.post(
        "/api/bindings",
        json={"trace_id": "trace-def", "harness_commit": harness_git.head},
    )
    binding = resp.json()["binding"]
    assert binding["llm_config"] is None
    assert binding["run_purpose"] == "production"
    assert binding["degraded"] is False
    assert binding["runtime_identity_digest"] is None


def test_binding_idempotent_does_not_overwrite(client, harness_git):
    first = client.post(
        "/api/bindings",
        json={"trace_id": "trace-idem", "harness_commit": harness_git.head},
    ).json()
    bound_at = first["binding"]["bound_at"]

    # 同 trace_id 重复签发：created=False，且新字段一概不覆盖（fail-static 补账铁律）
    second = client.post(
        "/api/bindings",
        json={
            "trace_id": "trace-idem",
            "harness_commit": harness_git.head,
            "llm_config": {"model": "other", "base_url": "http://other"},
            "run_purpose": "optimization",
            "degraded": True,
            "runtime_identity_digest": "rid-2",
        },
    ).json()

    assert second["created"] is False
    assert second["binding"] == first["binding"]
    assert second["binding"]["bound_at"] == bound_at
    assert second["binding"]["llm_config"] is None
    assert second["binding"]["degraded"] is False
    assert second["binding"]["run_purpose"] == "production"


def test_unknown_commit_creates_manifest_without_artifact(
    client, platform_env, harness_git
):
    """A/B 候选 Run 绑定场景：commit 不在 manifests 表 → 自动建档（算指纹，不打包）。"""
    def mutate(work):
        (work / "prompts" / "x.md").write_text("# prompt v2\n", encoding="utf-8")

    candidate = harness_git.commit("candidate prompt change", mutate=mutate)

    resp = client.post(
        "/api/bindings",
        json={"trace_id": "trace-ab", "harness_commit": candidate},
    )
    assert resp.status_code == 200
    assert resp.json()["created"] is True

    ledger = Ledger(platform_env.db)
    manifest = ledger.get_manifest_by_commit(candidate)
    assert manifest is not None
    assert manifest.surface_fingerprint.a_text["prompts/x.md"]
    assert manifest.artifact_digest is None  # 不打包
    assert not artifact_path(candidate).exists()


def test_get_binding_missing_404(client):
    assert client.get("/api/bindings/nope").status_code == 404


def test_binding_unknown_commit_in_bare_repo_400(client):
    resp = client.post(
        "/api/bindings",
        json={
            "trace_id": "trace-bad",
            "harness_commit": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        },
    )
    # commit 不在 bare repo：checkout 失败 → 400（参数问题），不是 500
    assert resp.status_code == 400
