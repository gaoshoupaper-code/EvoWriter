"""/api 路由（内网信任域，无鉴权——与 executor /internal 同模式）。

端点清单见 contracts/platform/__init__.py。路径参数 /artifacts/{commit} 的
commit 接受完整 hash（7 位短 hash 由 git checkout 决定是否可用，账本键以
调用方传入的字符串为准）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from contracts.platform import (
    ArtifactMeta,
    BindingAck,
    BindingCreate,
    BindingRecord,
    ProbeRequest,
    ProbeResult,
    ProductionStatus,
    PromoteRequest,
    PromoteResult,
    ResumeCheck,
)

from app import artifacts, bindings, release
from app.agent.probe import probe_candidate
from app.core.ledger import ledger_from_settings
from app.manifests import ensure_manifest

router = APIRouter(prefix="/api", tags=["platform"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/production", response_model=ProductionStatus)
def get_production() -> ProductionStatus:
    """当前生产版本状态（Runtime 冷启动对账源）。无生产版本 → 404。"""
    ledger = ledger_from_settings()
    production = ledger.get_production()
    if production is None:
        raise HTTPException(status_code=404, detail="尚无生产版本")
    # FR-001 导入的生产版本没有 manifest（导入不扫指纹）——惰性补建档，
    # 保证返回的 surface_fingerprint 是真实指纹而非空值
    manifest = ensure_manifest(ledger, production.commit)
    return ProductionStatus(
        version=production.version,
        commit=production.commit,
        artifact_digest=manifest.artifact_digest,
        surface_fingerprint=manifest.surface_fingerprint,
        promoted_at=production.promoted_at,
    )


@router.get("/artifacts/{commit}/meta", response_model=ArtifactMeta)
def get_artifact_meta(commit: str) -> ArtifactMeta:
    """artifact 元数据。已打包直接返回；未打包但账本认识该 commit → 惰性打包；
    完全未知 commit → 404（不盲目对任意字符串做 git clone）。"""
    meta = artifacts.find_meta(commit)
    if meta is not None:
        return meta
    ledger = ledger_from_settings()
    if ledger.get_manifest_by_commit(commit) is None:
        # FR-001 导入的历史版本只写 versions 不建 manifest——versions 命中时
        # 先按 issue_binding 同语义补建 manifest（checkout + 算指纹），A/B 存量
        # 线才拉得到 meta；versions 也查无才是真正未知 commit
        if not ledger.has_version_commit(commit):
            raise HTTPException(status_code=404, detail=f"commit {commit} 未打包且无 manifest")
        ensure_manifest(ledger, commit)
    # 惰性打包：该路径只消费 meta（回填 digest + 返回），指纹无消费者 → 跳过
    built = artifacts.build_artifact(commit, include_fingerprint=False)
    ledger.fill_artifact_digest(commit, built.meta.digest)
    return built.meta


@router.get("/artifacts/{commit}/download")
def download_artifact(commit: str) -> FileResponse:
    """下载 tar.gz（executor 校验 digest 后装配，DEC-012）。未打包 → 404。"""
    path = artifacts.artifact_path(commit)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"commit {commit} 未打包")
    return FileResponse(path, media_type="application/gzip", filename=f"{commit}.tar.gz")


@router.post("/bindings", response_model=BindingAck)
def create_binding(body: BindingCreate) -> BindingAck:
    """Run 绑定签发（幂等：同 trace_id 返回既有记录 created=False）。"""
    try:
        return bindings.issue_binding(body)
    except RuntimeError as exc:
        # commit 不在 bare repo 等 checkout 失败 → 400（调用方参数问题，非服务故障）
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/bindings/{trace_id}", response_model=BindingRecord)
def get_binding(trace_id: str) -> BindingRecord:
    record = bindings.get_binding(trace_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"trace {trace_id} 无绑定记录")
    return record


@router.get("/bindings/{trace_id}/resume-check", response_model=ResumeCheck)
def get_resume_check(trace_id: str) -> ResumeCheck:
    """resume 兼容门禁（HITL 恢复前调）。无绑定 → 404。"""
    result = bindings.resume_check(trace_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"trace {trace_id} 无绑定记录")
    return result


@router.post("/release/probe", response_model=ProbeResult)
def probe(body: ProbeRequest) -> ProbeResult:
    """发版门禁：候选 commit 干净 checkout + 真实装配。"""
    return probe_candidate(body.source_commit)


@router.post("/release/promote", response_model=PromoteResult)
def promote(body: PromoteRequest) -> PromoteResult:
    """晋升生产。前置：该 commit 必须 probe ready（promote 内部重跑门禁）。"""
    try:
        return release.promote(body.source_commit, body.version_note)
    except release.ProbeRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
