"""probe / promote / reload 通知 / resume-check 集成测试（TestClient 走真实 lifespan）。"""
from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from app import release as release_mod
from app.artifacts import artifact_path
from app.core.ledger import Ledger


class _FakeResponse:
    status_code = 200


def _remove_snapshot(work):
    (work / "middleware" / "artifact_snapshot.py").unlink()


# ── probe 门禁 ────────────────────────────────────────────────────


def test_probe_ready(client, harness_git):
    resp = client.post(
        "/api/release/probe", json={"source_commit": harness_git.head}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    identity = body["runtime_identity"]
    assert identity["harness_commit"] == harness_git.head
    assert identity["harness_dirty"] is False
    assert identity["artifact_snapshot_middleware"] is True


def test_probe_rejected_when_artifact_snapshot_missing(client, harness_git):
    bad = harness_git.commit("drop artifact_snapshot", mutate=_remove_snapshot)
    resp = client.post("/api/release/probe", json={"source_commit": bad})
    assert resp.status_code == 200  # 门禁拒绝是正常业务结果，不是 5xx
    body = resp.json()
    assert body["status"] == "rejected"
    assert "artifact_snapshot" in body["reason"]


def test_probe_rejected_when_commit_not_in_bare(client):
    resp = client.post(
        "/api/release/probe",
        json={"source_commit": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def _rewrite_init(text: str):
    """构造 mutate：整包替换 __init__.py 为指定源码。"""

    def mutate(work):
        (work / "__init__.py").write_text(text, encoding="utf-8")

    return mutate


_ASSEMBLE_NONE = '''\
"""assemble 返回 None 的坏包（review #7 fixture）。"""
from contracts.runtime_context import RuntimeContext


def assemble(ctx: RuntimeContext):
    return None
'''

_ASSEMBLE_SIDE_EFFECT = '''\
"""assemble 期间写新文件的副作用包（review #8 fixture，非 .pyc）。"""
from contracts.runtime_context import RuntimeContext
from pathlib import Path


def assemble(ctx: RuntimeContext):
    (Path(__file__).parent / "side_effect.txt").write_text("x", encoding="utf-8")
    return {"assembled": True}
'''


def test_probe_rejected_when_assemble_returns_none(client, harness_git):
    """assemble 契约返回 None → 门禁只剩「不抛异常」不够，必须 rejected。"""
    bad = harness_git.commit("assemble returns none", mutate=_rewrite_init(_ASSEMBLE_NONE))
    resp = client.post("/api/release/probe", json={"source_commit": bad})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert "assemble" in body["reason"]


def test_probe_rejected_when_assemble_writes_untracked_file(client, harness_git):
    """装配副作用写新文件（untracked，非 .pyc）→ 全量 dirty 门禁拒。"""
    bad = harness_git.commit(
        "assemble side effect", mutate=_rewrite_init(_ASSEMBLE_SIDE_EFFECT),
    )
    resp = client.post("/api/release/probe", json={"source_commit": bad})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert "不干净" in body["reason"]


# ── promote 晋升 ──────────────────────────────────────────────────


def _mock_notify_ok(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse()

    monkeypatch.setattr(release_mod.httpx, "post", fake_post)
    monkeypatch.setattr(release_mod.time, "sleep", lambda _s: None)
    return calls


def test_promote_success(client, platform_env, harness_git, monkeypatch):
    calls = _mock_notify_ok(monkeypatch)
    ledger = Ledger(platform_env.db)
    # lifespan 的 FR-001 导入已写入 registry v1 → 下一个版本号 = 2
    expected_version = ledger.max_version() + 1

    resp = client.post(
        "/api/release/promote",
        json={"source_commit": harness_git.head, "version_note": "first promote"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == expected_version
    assert body["commit"] == harness_git.head
    assert body["artifact_digest"]
    assert body["reload_notified"] is True

    # reload 通知：POST {executor_url}/internal/platform/reload，body=HarnessReloadNotice
    assert len(calls) == 1
    assert calls[0]["url"] == "http://executor-test:7788/internal/platform/reload"
    assert calls[0]["json"] == {
        "version": expected_version,
        "commit": harness_git.head,
        "artifact_digest": body["artifact_digest"],
    }

    # 账本：versions 追加、production 指针切换、artifact 落盘
    versions = ledger.list_versions()
    assert versions[-1]["version"] == expected_version
    assert versions[-1]["commit"] == harness_git.head
    assert versions[-1]["note"] == "first promote"
    production = ledger.get_production()
    assert (production.version, production.commit) == (expected_version, harness_git.head)
    assert artifact_path(harness_git.head).exists()

    # GET /api/production 对账一致
    status = client.get("/api/production").json()
    assert status["version"] == expected_version
    assert status["commit"] == harness_git.head
    assert status["artifact_digest"] == body["artifact_digest"]
    assert status["surface_fingerprint"]["c_code"]


def test_promote_blocked_when_probe_rejects(client, platform_env, harness_git):
    bad = harness_git.commit("drop artifact_snapshot", mutate=_remove_snapshot)
    resp = client.post("/api/release/promote", json={"source_commit": bad})
    assert resp.status_code == 409
    assert "artifact_snapshot" in resp.json()["detail"]

    # 晋升被门禁拦截 → 账本不动（仍是 FR-001 导入的 v1，无新版本）
    ledger = Ledger(platform_env.db)
    assert ledger.get_production().version == 1
    assert ledger.max_version() == 1


def test_promote_notify_failure_not_fatal(client, platform_env, harness_git, monkeypatch):
    """reload 通知 3 次全失败 → promote 仍成功，reload_notified=False。"""
    attempts = []

    def failing_post(url, json=None, headers=None, timeout=None):
        attempts.append(url)
        raise httpx.ConnectError("executor down")

    monkeypatch.setattr(release_mod.httpx, "post", failing_post)
    monkeypatch.setattr(release_mod.time, "sleep", lambda _s: None)

    resp = client.post(
        "/api/release/promote", json={"source_commit": harness_git.head}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reload_notified"] is False
    assert body["artifact_digest"]  # 账本与 artifact 不受通知失败影响
    assert len(attempts) == release_mod.NOTIFY_ATTEMPTS == 3
    assert Ledger(platform_env.db).get_production().version == body["version"]


# ── resume-check 兼容判定 ────────────────────────────────────────


def test_resume_check_after_promotes(client, harness_git):
    # trace-a 绑定在初始版本
    client.post(
        "/api/bindings",
        json={"trace_id": "trace-a", "harness_commit": harness_git.head},
    )

    # c2：只改 prompts → promote → trace-a 应 compatible
    def touch_prompt(work):
        (work / "prompts" / "x.md").write_text("# prompt v2\n", encoding="utf-8")

    c2 = harness_git.commit("prompt tweak", mutate=touch_prompt)
    promoted = client.post("/api/release/promote", json={"source_commit": c2}).json()

    check_a = client.get("/api/bindings/trace-a/resume-check").json()
    assert check_a["decision"] == "compatible"
    assert check_a["bound_commit"] == harness_git.head
    assert check_a["current_commit"] == c2
    assert check_a["changed_layers"] == ["a_text"]
    assert "prompts/x.md" in check_a["changed_files"]

    # trace-b 绑定在 c2
    client.post(
        "/api/bindings", json={"trace_id": "trace-b", "harness_commit": c2}
    )

    # c3：改 middleware 代码 → promote → trace-b 应 incompatible（c_code 变化）
    def touch_middleware(work):
        (work / "middleware" / "artifact_snapshot.py").write_text(
            "def build_snapshot(*a, **k):\n    return {'v': 2}\n", encoding="utf-8"
        )

    c3 = harness_git.commit("middleware change", mutate=touch_middleware)
    client.post("/api/release/promote", json={"source_commit": c3})

    check_b = client.get("/api/bindings/trace-b/resume-check").json()
    assert check_b["decision"] == "incompatible"
    assert "c_code" in check_b["changed_layers"]
    assert "middleware/artifact_snapshot.py" in check_b["changed_files"]

    # trace-a（旧版本）在 c3 下：a+c 两层都变 → 同样 incompatible
    check_a2 = client.get("/api/bindings/trace-a/resume-check").json()
    assert check_a2["decision"] == "incompatible"
    assert set(check_a2["changed_layers"]) == {"a_text", "c_code"}
    assert promoted["version"] >= 1


def test_resume_check_missing_binding_404(client):
    assert client.get("/api/bindings/nope/resume-check").status_code == 404


def test_resume_check_without_production_is_incompatible(
    tmp_path, monkeypatch
):
    """绑定存在但账本无生产指针（未 promote、未导入）→ incompatible，reason 写明。"""
    from contracts.platform import SurfaceFingerprint

    from app.core.ledger import Ledger as L
    from app.main import app
    from tests_helpers import configure_platform_env

    # 空目录 bare：lifespan 跳过 FR-001 导入 → 无 production
    empty_bare = tmp_path / "empty.git"
    empty_bare.mkdir()
    paths = configure_platform_env(monkeypatch, tmp_path, empty_bare)

    with TestClient(app) as c:
        # 账本里手工放一条绑定（绕过 git：绑定服务需要 bare repo，这里只测判定分支）
        ledger = L(paths.db)
        manifest = ledger.create_manifest("fakecommit", SurfaceFingerprint())
        ledger.create_binding(
            trace_id="trace-noprod",
            manifest_id=manifest.manifest_id,
            harness_commit="fakecommit",
            llm_config=None,
            run_purpose="production",
            degraded=False,
            runtime_identity_digest=None,
        )

        resp = c.get("/api/bindings/trace-noprod/resume-check")
        assert resp.status_code == 200
        body = resp.json()
        assert body["decision"] == "incompatible"
        assert "无生产版本" in body["reason"]
        assert body["current_commit"] == ""


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_production_404_when_empty(tmp_path, monkeypatch):
    from app.main import app
    from tests_helpers import configure_platform_env

    empty_bare = tmp_path / "empty.git"
    empty_bare.mkdir()
    configure_platform_env(monkeypatch, tmp_path, empty_bare)
    with TestClient(app) as c:
        assert c.get("/api/production").status_code == 404


def test_probe_ready_with_thin_wrapper_importing_executor_code(client, harness_git):
    """薄包装 harness（import executor 的 app.*）必须能在 platform 门禁真实装配。

    生产 harness 是薄包装：assemble 链 import app.platform.agent.runtime 等
    executor 模块（Phase 7 迁移中间态）。probe 子进程把 executor 目录注入
    PYTHONPATH 后必须能解析这些 import——这是 DEC-007（门禁不寄生生产
    Runtime、装配环境与 Runtime 同代码）成立的前提证明。
    """
    def _make_thin(work) -> None:
        (work / "__init__.py").write_text(
            "from app.platform.agent.runtime import FilesystemPermission\n"
            "from contracts.runtime_context import RuntimeContext\n\n\n"
            "def assemble(ctx: RuntimeContext):\n"
            "    return {'assembled': True, 'perm': FilesystemPermission}\n",
            encoding="utf-8",
        )

    commit = harness_git.commit("thin wrapper", mutate=_make_thin)
    resp = client.post("/api/release/probe", json={"source_commit": commit})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready", body.get("reason")
    assert body["runtime_identity"]["harness_commit"] == commit
