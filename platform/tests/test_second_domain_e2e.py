"""第二垂直领域（翻译）e2e：平台零改动接纳新领域（REQ-20260919-202344 FR-008/AC-010）。

fixture 与 tests_helpers.make_harness_repo 同构，但包内容换成拷贝
platform/examples/harness-translation/（建独立工作库 + bare）。
五个用例走完整链路：门禁 probe → promote → 绑定 → resume 兼容判定 →
复现一致性 → surface 指纹覆盖。全部通过即证明 executor / contracts 核心 /
Platform 门禁逻辑对新领域零改动（验收即「不改而通过」）。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app import release as release_mod
from tests_helpers import (
    REGISTRY_CREATED_AT,
    HarnessGit,
    _init_work_repo,
    configure_platform_env,
    git_run,
)

# 第二领域示例包源（platform/examples/harness-translation/）
EXAMPLE_PKG = Path(__file__).resolve().parents[1] / "examples" / "harness-translation"


def make_translation_repo(tmp: Path) -> HarnessGit:
    """翻译领域 fixture：示例包拷进工作库，建独立 bare，registry v1 指向首个包 commit。

    两步 commit 与 make_harness_repo 一致：registry.json 引用 commit hash，
    自身也要被 commit——head = 包内容版本（probe/promote/绑定的目标）。
    """
    work = tmp / "translation_work"
    bare = tmp / "translation.git"
    work.mkdir()
    _init_work_repo(work)
    shutil.copytree(EXAMPLE_PKG, work, dirs_exist_ok=True)
    # 与真实 harness 仓库一致：忽略字节码缓存（probe 装配会生成 __pycache__）
    (work / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")

    bare.mkdir()
    git_run(["init", "-q", "--bare"], bare)
    git_run(["symbolic-ref", "HEAD", "refs/heads/main"], bare)
    git_run(["remote", "add", "origin", str(bare)], work)

    git_run(["add", "-A"], work)
    git_run(["commit", "-q", "-m", "init translation harness package"], work)
    head = git_run(["rev-parse", "HEAD"], work)

    registry = {
        "production": 1,
        "versions": [
            {
                "version": 1,
                "commit_hash": head,
                "change_summary": "init",
                "created_at": REGISTRY_CREATED_AT,
            }
        ],
    }
    (work / "registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    repo = HarnessGit(work=work, bare=bare, head=head)
    repo.commit("registry v1")
    return repo


# ── fixture（独立命名，不与写作领域 conftest fixture 纠缠）─────────


@pytest.fixture
def translation_git(tmp_path) -> HarnessGit:
    """tmp 内的翻译领域工作库 + 独立 bare（示例包 + registry v1）。"""
    return make_translation_repo(tmp_path)


@pytest.fixture
def translation_env(tmp_path, translation_git, monkeypatch):
    """PLATFORM_* env 指向翻译领域独立 bare，前后清 settings 缓存。"""
    paths = configure_platform_env(monkeypatch, tmp_path, translation_git.bare)
    yield paths
    from app.core.settings import get_settings

    get_settings.cache_clear()


@pytest.fixture
def translation_client(translation_env):
    """TestClient（进入时跑 lifespan：建表 + FR-001 初始导入）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


class _FakeResponse:
    status_code = 200


def _mock_notify_ok(monkeypatch):
    """promote 的 reload 通知打桩（测试环境无 executor 可达）。"""
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json})
        return _FakeResponse()

    monkeypatch.setattr(release_mod.httpx, "post", fake_post)
    monkeypatch.setattr(release_mod.time, "sleep", lambda _s: None)
    return calls


# ── 1. 门禁接纳（AC-010 核心）─────────────────────────────────────


def test_probe_accepts_second_domain(translation_client, translation_git):
    """Platform 门禁逻辑零改动即接纳翻译领域 harness（薄包装真实装配 ready）。"""
    resp = translation_client.post(
        "/api/release/probe", json={"source_commit": translation_git.head}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready", body.get("reason")
    assert body["runtime_identity"] == {
        "harness_commit": translation_git.head,
        "harness_dirty": False,
        "artifact_snapshot_middleware": True,
    }


# ── 2. 晋升 + 绑定 ────────────────────────────────────────────────


def test_promote_and_bind_second_domain(
    translation_client, translation_git, monkeypatch
):
    calls = _mock_notify_ok(monkeypatch)

    promoted = translation_client.post(
        "/api/release/promote",
        json={"source_commit": translation_git.head, "version_note": "translation v1"},
    )
    assert promoted.status_code == 200
    body = promoted.json()
    assert body["commit"] == translation_git.head
    assert body["artifact_digest"]
    assert body["reload_notified"] is True
    # reload 通知携带第二领域 commit（executor 消费方对领域无感知）
    assert calls[0]["url"] == "http://executor-test:7788/internal/platform/reload"
    assert calls[0]["json"]["commit"] == translation_git.head

    ack = translation_client.post(
        "/api/bindings",
        json={"trace_id": "translation-run-1", "harness_commit": translation_git.head},
    )
    assert ack.status_code == 200
    assert ack.json()["created"] is True

    got = translation_client.get("/api/bindings/translation-run-1")
    assert got.status_code == 200
    record = got.json()
    assert record["trace_id"] == "translation-run-1"
    assert record["harness_commit"] == translation_git.head
    assert record["manifest_id"] > 0
    assert record["run_purpose"] == "production"
    assert record["status"] == "active"
    assert record["bound_at"]


# ── 3. 兼容判定对第二领域生效（A 层 compatible / C 层 incompatible）──


def test_compat_gate_applies_to_second_domain(
    translation_client, translation_git, monkeypatch
):
    _mock_notify_ok(monkeypatch)
    head = translation_git.head

    # trace 绑定在初始版本
    translation_client.post(
        "/api/bindings", json={"trace_id": "trans-compat-a", "harness_commit": head}
    )

    # c2：只改 prompts（A 层）→ promote → 旧绑定应 compatible
    def touch_prompt(work):
        prompt = work / "prompts" / "translator_system.md"
        prompt.write_text(
            prompt.read_text(encoding="utf-8") + "\n## 术语表\n- 术语全文统一译法。\n",
            encoding="utf-8",
        )

    c2 = translation_git.commit("prompt tweak (a_text)", mutate=touch_prompt)
    promoted = translation_client.post(
        "/api/release/promote", json={"source_commit": c2}
    )
    assert promoted.status_code == 200

    check_a = translation_client.get(
        "/api/bindings/trans-compat-a/resume-check"
    ).json()
    assert check_a["decision"] == "compatible"
    assert check_a["bound_commit"] == head
    assert check_a["current_commit"] == c2
    assert check_a["changed_layers"] == ["a_text"]
    assert "prompts/translator_system.md" in check_a["changed_files"]

    # trace-b 绑定在 c2
    translation_client.post(
        "/api/bindings", json={"trace_id": "trans-compat-b", "harness_commit": c2}
    )

    # c3：改 middleware 代码（C 层）→ promote → trace-b 应 incompatible
    def touch_middleware(work):
        mw = work / "middleware" / "artifact_snapshot.py"
        mw.write_text(
            mw.read_text(encoding="utf-8")
            + "\n# 翻译领域取证调优标记（c_code 变更示例）\n",
            encoding="utf-8",
        )

    c3 = translation_git.commit("middleware change (c_code)", mutate=touch_middleware)
    assert translation_client.post(
        "/api/release/promote", json={"source_commit": c3}
    ).status_code == 200

    check_b = translation_client.get(
        "/api/bindings/trans-compat-b/resume-check"
    ).json()
    assert check_b["decision"] == "incompatible"
    assert "c_code" in check_b["changed_layers"]
    assert "middleware/artifact_snapshot.py" in check_b["changed_files"]

    # 旧绑定（trace-a）在 c3 下：a + c 两层都变 → 同样 incompatible
    check_a2 = translation_client.get(
        "/api/bindings/trans-compat-a/resume-check"
    ).json()
    assert check_a2["decision"] == "incompatible"
    assert set(check_a2["changed_layers"]) == {"a_text", "c_code"}


# ── 4. 复现一致性（AC-009 链）────────────────────────────────────


def test_probe_reproducible_identity(translation_client, translation_git):
    """同 commit 两次 probe → runtime_identity 一致且 ready（指纹稳定）。"""
    identities = []
    for _ in range(2):
        body = translation_client.post(
            "/api/release/probe", json={"source_commit": translation_git.head}
        ).json()
        assert body["status"] == "ready", body.get("reason")
        identities.append(body["runtime_identity"])
    assert identities[0] == identities[1]
    assert identities[0]["harness_commit"] == translation_git.head


# ── 5. surface 指纹覆盖第二领域文件 ───────────────────────────────


def test_surface_fingerprint_covers_second_domain(
    translation_client, translation_git, monkeypatch
):
    _mock_notify_ok(monkeypatch)
    resp = translation_client.post(
        "/api/release/promote", json={"source_commit": translation_git.head}
    )
    assert resp.status_code == 200

    status = translation_client.get("/api/production")
    assert status.status_code == 200
    fp = status.json()["surface_fingerprint"]
    # A 层：领域 prompt 与 skill 文本进指纹
    assert fp["a_text"]["prompts/translator_system.md"]
    assert fp["a_text"]["skills/translator/SKILL.md"]
    # C 层：包内 Python 进指纹（assemble 入口 + middleware 薄包装）
    assert fp["c_code"]["__init__.py"]
    assert fp["c_code"]["middleware/artifact_snapshot.py"]
