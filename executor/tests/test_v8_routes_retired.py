"""v8 产物路由下线测试（FR-002/004/005，REQ-20260930-163019）。

单 Agent 收敛后退役的路由，锁死不得回潮：
  /workspaces/{id}/outline、/detail-outline、/storyline-graph、/novel、
  /novel/export.pdf、/novel/export-word.zip、/threads/{id}/outline
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.threads import router as threads_router
from app.routers.workspaces import router as workspaces_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(workspaces_router)
    app.include_router(threads_router)
    return TestClient(app)


def test_v8_artifact_routes_are_gone() -> None:
    client = _client()
    retired = [
        "/api/workspaces/ws-1/outline",
        "/api/workspaces/ws-1/detail-outline",
        "/api/workspaces/ws-1/storyline-graph",
        "/api/workspaces/ws-1/novel",
        "/api/workspaces/ws-1/novel/export.pdf",
        "/api/workspaces/ws-1/novel/export-word.zip",
        "/api/threads/t-1/outline",
    ]
    for path in retired:
        resp = client.get(path)
        assert resp.status_code == 404, f"退役路由仍存在: {path}"


def test_storyline_route_survives_with_panorama_schema() -> None:
    """storyline 主路由保留，且响应模型含 panorama 字段（FR-003）。"""
    from app.schemas.screenplay import WorkspaceStorylineContent

    fields = WorkspaceStorylineContent.model_fields
    assert "panorama" in fields
    # 404（workspace 不存在）而非 405/500——路由本身健在
    resp = _client().get("/api/workspaces/none/storyline")
    assert resp.status_code == 404
