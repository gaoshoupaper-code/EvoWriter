"""GET /api/versions 端点测试（评测版本下拉数据源，账本只读视图）。"""
from __future__ import annotations

from app import release as release_mod


class _FakeResponse:
    status_code = 200


def _mock_notify_ok(monkeypatch):
    """reload 通知打桩（避免真连 executor + 3×2s 重试等待）。"""
    monkeypatch.setattr(
        release_mod.httpx,
        "post",
        lambda url, json=None, headers=None, timeout=None: _FakeResponse(),
    )
    monkeypatch.setattr(release_mod.time, "sleep", lambda _s: None)


def test_versions_lists_imported_ledger(client, harness_git):
    resp = client.get("/api/versions")
    assert resp.status_code == 200
    body = resp.json()
    # lifespan 已把 fixture registry v1 导入账本
    assert body["production_version"] == 1
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["version"] == 1
    assert item["commit"]  # runner 按此 commit 触发 executor
    assert item["note"] == "init"  # fixture registry v1 的 change_summary
    assert item["created_at"]


def test_versions_reflects_promote(client, platform_env, harness_git, monkeypatch):
    _mock_notify_ok(monkeypatch)
    promoted = client.post(
        "/api/release/promote",
        json={"source_commit": harness_git.head, "version_note": "二次发版"},
    ).json()

    body = client.get("/api/versions").json()
    assert body["production_version"] == promoted["version"]
    versions = [v["version"] for v in body["items"]]
    assert versions == sorted(versions, reverse=True)  # 版本号倒序
    item = next(v for v in body["items"] if v["version"] == promoted["version"])
    assert item["commit"] == harness_git.head
    assert item["note"] == "二次发版"
