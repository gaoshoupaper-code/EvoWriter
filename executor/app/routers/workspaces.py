"""workspaces 路由（PR-14 从 main.py 抽出）。

工作区 CRUD + 产物读取（storyline/worldview/characters）+ SSE watch。
v8 产物路由（outline/detail-outline/storyline-graph/novel 及导出）已随
单故事专家收敛退役（REQ-20260930-163019 FR-002/004/005）——storyline 响应
自带 panorama（跨线全景事件，FR-003）。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from watchfiles import awatch

from app.auth import CurrentUser, current_user
from app.domains.writing.expert_agent.services.storyline_graph import (
    build_panorama_events,
    build_rhythm_data,
)
from app.routers.context import _log, get_agent_service, get_character_service, get_thread_store
from app.schemas.screenplay import (
    Annotation,
    AnnotationClearRequest,
    AnnotationCreateRequest,
    AnnotationDispatchResponse,
    AnnotationsListResponse,
    AnnotationUpdateRequest,
    ArtifactRedoDeleteRequest,
    ArtifactRedoDeleteResponse,
    OutlineFileEntry,
    PanoramaEvent,
    HookProgress,
    ManuscriptContent,
    ManuscriptSaveRequest,
    RhythmDataModel,
    RhythmPoint,
    ShapeSlotModel,
    WorkspaceBootstrapResponse,
    WorkspaceCharacterContent,
    WorkspaceCreateRequest,
    WorkspaceObjectContent,
    WorkspaceOutlineContent,
    WorkspaceStorylineContent,
    WorkspaceSummary,
    WorkspaceWorldviewContent,
    WritingStageUpdateRequest,
    WritingStageAdvanceRequest,
    WritingModeUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# outline 产物目录内文件上限（防异常 workspace 撑爆载荷）。五段制长跑百章级
# 网文 章纲+正文 可达数百文件（C7 修复：200→2000；legacy 工作区沿用小上限）
_MAX_OUTLINE_FILES = 200
_MAX_FIVE_FILES = 2000


def _read_outline_content(workspace_path: Path) -> WorkspaceOutlineContent:
    """五段产物树全集 + 管线阶段条数据（REQ-20261018-115508 FR-011，DEC-011）。

    五段制工作区：kind=five，扫描根级五段产物（主题/总纲/文风 + 卷纲/章纲/正文）。
    旧七步/三步制工作区：kind=legacy + files 照常返回 outline/（只读查看）。
    旧五件套工作区：kind=legacy、files 为空（内容走旧面板）。
    """
    from app.domains.writing.pipeline import classify_workspace, pipeline_status
    from contracts.writing_artifacts import (
        CHAPTER_OUTLINE_DIR,
        MASTER_FILENAME,
        STYLE_FILENAME,
        TEXT_DIR,
        THEME_FILENAME,
        VOLUME_DIR,
    )

    kind = classify_workspace(workspace_path)
    if kind == "five":
        files: list[OutlineFileEntry] = []
        cap = _MAX_FIVE_FILES
        candidates = [
            workspace_path / THEME_FILENAME,
            workspace_path / MASTER_FILENAME,
            workspace_path / STYLE_FILENAME,
        ]
        for d in (workspace_path / VOLUME_DIR, workspace_path / CHAPTER_OUTLINE_DIR,
                  workspace_path / TEXT_DIR):
            if d.is_dir():
                candidates.extend(sorted(d.glob("*.md"), key=lambda p: p.as_posix()))
        for f in candidates:
            if not f.is_file() or len(files) >= cap:
                continue
            try:
                files.append(OutlineFileEntry(
                    rel=f.relative_to(workspace_path).as_posix(),
                    markdown=f.read_text(encoding="utf-8"),
                ))
            except OSError:
                continue
        return WorkspaceOutlineContent(
            kind="five", pipeline=pipeline_status(workspace_path), files=files
        )
    if not (workspace_path / "outline").is_dir():
        return WorkspaceOutlineContent(kind="legacy", pipeline={"kind": "legacy"})
    legacy_files: list[OutlineFileEntry] = []
    roots = [
        workspace_path / "outline",
        workspace_path / "worldview",
    ]
    allowed_extra = {"core.md", "setting.md"}
    for root in roots:
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*.md"), key=lambda p: p.as_posix()):
            rel = f.relative_to(workspace_path).as_posix()
            if root.name == "worldview" and f.name not in allowed_extra:
                continue  # legacy worldview.md 走旧面板
            if len(legacy_files) >= _MAX_OUTLINE_FILES:
                break
            try:
                legacy_files.append(OutlineFileEntry(rel=rel, markdown=f.read_text(encoding="utf-8")))
            except OSError:
                continue
    return WorkspaceOutlineContent(
        kind="legacy", pipeline=pipeline_status(workspace_path), files=legacy_files
    )


def _attach_panorama(
    content: WorkspaceStorylineContent | None, workspace_path: Path
) -> WorkspaceStorylineContent | None:
    """给 v2 storyline 内容补跨线全景事件（FR-003）+ 节奏数据（FR-006）。

    组装在路由层完成——platform 层（artifact_store）禁止依赖 domains 层解析器
    （分层规则 R1）。旧格式（legacy）不解析：panorama/rhythm 保持空，前端降级。
    """
    if content is None or content.format != "v2":
        return content
    events = build_panorama_events(workspace_path)
    if events is not None:
        content.panorama = [
            PanoramaEvent(
                t=ev.t_raw, name=ev.name, type=ev.type, storylines=list(ev.storylines),
                characters=ev.characters, location=ev.location, desc=ev.desc,
                tension=ev.tension, payoff=ev.payoff,
            )
            for ev in events
        ]
    rhythm = build_rhythm_data(workspace_path)
    if rhythm is not None:
        content.rhythm = RhythmDataModel(
            mainline=[
                RhythmPoint(t=p.t, name=p.name, tension=p.tension, payoff=p.payoff, line=p.line, surface=p.surface)
                for p in rhythm.mainline
            ],
            synthesis=[
                RhythmPoint(t=p.t, name=p.name, tension=p.tension, payoff=p.payoff, line=p.line, surface=p.surface)
                for p in rhythm.synthesis
            ],
            dark={
                line: [
                    RhythmPoint(t=p.t, name=p.name, tension=p.tension, payoff=p.payoff, line=p.line, surface=p.surface)
                    for p in pts
                ]
                for line, pts in rhythm.dark.items()
            },
            shape_slots=[
                ShapeSlotModel(slot=s.slot, op=s.op, values=list(s.values), twin_peak=s.twin_peak)
                for s in rhythm.shape_slots
            ],
            hooks=[
                HookProgress(
                    id=r.id, text=r.text, level=r.level, type=r.type, status=r.status,
                    plant_events=list(r.plant_events), progress_events=list(r.progress_events),
                    payoff_events=list(r.payoff_events), note=r.note,
                )
                for r in rhythm.hooks
            ],
        )
    return content


# ════════════════════════════════════════════════════════════
# SSE watch 辅助
# ════════════════════════════════════════════════════════════

def _sse_event(event_type: str, payload: object) -> str:
    data = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {event_type}\ndata: {data}\n\n"


def _classify_changes(changes, workspace_path: Path) -> set[str]:
    categories: set[str] = set()
    for _change_type, path_str in changes:
        try:
            rel = Path(path_str).relative_to(workspace_path)
        except ValueError:
            continue
        parts = rel.parts
        if not parts:
            continue
        top = parts[0]
        # 五段制产物树（REQ-20261018-115508）：根级三件 + 三个子目录都走 outline 事件
        if top in ("主题.md", "总纲.md", "文风.md", "outline", "卷纲", "章纲", "正文"):
            categories.add("outline")
        elif top in ("storyline.md", "timeline.md", "hooks.md") or (len(parts) > 1 and parts[0] == "storyline"):
            categories.add("storyline")
        elif top == "worldview.md":
            categories.add("worldview")
        elif top == "worldview" and rel.name in ("core.md", "setting.md"):
            categories.add("outline")
        elif len(parts) > 1 and parts[0] == "character":
            categories.add("characters")
        elif len(parts) > 1 and parts[0] == "object":
            categories.add("objects")
    return categories


async def _workspace_watch_generator(owner_id: str, workspace_id: str, workspace_path: Path):
    thread_store = get_thread_store()
    _log("sse_open", channel="watch", workspace_id=workspace_id)
    start = time.perf_counter()
    try:
        async for changes in awatch(
            workspace_path,
            watch_filter=lambda _change, path: Path(path).suffix == ".md",
            debounce=400, step=50, recursive=True, ignore_permission_denied=True,
        ):
            categories = _classify_changes(changes, workspace_path)
            if not categories:
                continue
            if "outline" in categories:
                # 产物变更 → 标注锚点复验（REQ-20261027-213246 FR-002）：
                # 尽力而为，复验失败不影响 outline 推送本身。
                try:
                    from app.domains.writing.annotations import reverify_annotations

                    reverify_annotations(workspace_path)
                except Exception:  # pragma: no cover - 文件竞态等边缘，降级为跳过
                    logger.warning("标注复验失败: workspace=%s", workspace_id, exc_info=True)
                yield _sse_event("outline", _read_outline_content(workspace_path).model_dump())
            if "storyline" in categories:
                content = thread_store.artifacts.read_workspace_storyline(owner_id, workspace_id)
                if content is not None:
                    yield _sse_event("storyline", content.model_dump())
            if "worldview" in categories:
                content = thread_store.artifacts.read_workspace_worldview(owner_id, workspace_id)
                if content is not None:
                    yield _sse_event("worldview", content.model_dump())
            if "characters" in categories:
                content = thread_store.artifacts.read_workspace_characters(owner_id, workspace_id)
                if content is not None:
                    yield _sse_event("characters", content.model_dump())
            if "objects" in categories:
                content = thread_store.artifacts.read_workspace_objects(owner_id, workspace_id)
                if content is not None:
                    yield _sse_event("objects", content.model_dump())
        _log("sse_close", channel="watch", workspace_id=workspace_id,
             ms=int((time.perf_counter() - start) * 1000))
    except BaseException as exc:
        _log("sse_error", channel="watch", workspace_id=workspace_id,
             error=type(exc).__name__, ms=int((time.perf_counter() - start) * 1000))
        raise


# ════════════════════════════════════════════════════════════
# 端点
# ════════════════════════════════════════════════════════════

@router.get("/workspaces/{workspace_id}/bootstrap", response_model=WorkspaceBootstrapResponse)
def bootstrap_workspace(workspace_id: str, user: CurrentUser = Depends(current_user)) -> WorkspaceBootstrapResponse:
    thread_store = get_thread_store()
    data = thread_store.bootstrap_workspace(user.user_id, workspace_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace = thread_store.get_workspace(user.user_id, workspace_id)
    if workspace is not None:
        data["storyline"] = _attach_panorama(data.get("storyline"), Path(workspace.workspace_path))
        data["outline"] = _read_outline_content(Path(workspace.workspace_path))
    return WorkspaceBootstrapResponse(**data)


@router.get("/workspaces", response_model=list[WorkspaceSummary])
def list_workspaces(user: CurrentUser = Depends(current_user)) -> list[WorkspaceSummary]:
    return get_thread_store().list_workspaces(user.user_id)


@router.post("/workspaces", response_model=WorkspaceSummary)
def create_workspace(payload: WorkspaceCreateRequest, user: CurrentUser = Depends(current_user)) -> WorkspaceSummary:
    thread_store = get_thread_store()
    try:
        return thread_store.create_workspace(user.user_id, payload.title, payload.domain)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/workspaces/{workspace_id}/storyline", response_model=WorkspaceStorylineContent)
def get_workspace_storyline(workspace_id: str, user: CurrentUser = Depends(current_user)) -> WorkspaceStorylineContent:
    thread_store = get_thread_store()
    content = thread_store.artifacts.read_workspace_storyline(user.user_id, workspace_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace = thread_store.get_workspace(user.user_id, workspace_id)
    if workspace is not None:
        content = _attach_panorama(content, Path(workspace.workspace_path))
    return content


@router.get("/workspaces/{workspace_id}/worldview", response_model=WorkspaceWorldviewContent)
def get_workspace_worldview(workspace_id: str, user: CurrentUser = Depends(current_user)) -> WorkspaceWorldviewContent:
    content = get_thread_store().artifacts.read_workspace_worldview(user.user_id, workspace_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return content


@router.get("/workspaces/{workspace_id}/characters", response_model=WorkspaceCharacterContent)
def get_workspace_characters(workspace_id: str, user: CurrentUser = Depends(current_user)) -> WorkspaceCharacterContent:
    content = get_thread_store().artifacts.read_workspace_characters(user.user_id, workspace_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return content


@router.get("/workspaces/{workspace_id}/objects", response_model=WorkspaceObjectContent)
def get_workspace_objects(workspace_id: str, user: CurrentUser = Depends(current_user)) -> WorkspaceObjectContent:
    content = get_thread_store().artifacts.read_workspace_objects(user.user_id, workspace_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return content


@router.get("/workspaces/{workspace_id}/outline", response_model=WorkspaceOutlineContent)
def get_workspace_outline(workspace_id: str, user: CurrentUser = Depends(current_user)) -> WorkspaceOutlineContent:
    """五段产物树全集 + 管线阶段条（REQ-20261018-115508 FR-011；legacy 返回 kind=legacy）。"""
    thread_store = get_thread_store()
    workspace = thread_store.get_workspace(user.user_id, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return _read_outline_content(Path(workspace.workspace_path))


@router.put("/workspaces/{workspace_id}/writing/stage")
def update_writing_stage(
    workspace_id: str, payload: WritingStageUpdateRequest,
    user: CurrentUser = Depends(current_user),
) -> dict:
    """人工段指针写入（REQ-20261019-232617 FR-002/004）——前进/跳段/回退统一入口。

    段间移动的唯一通道：只由界面人工确认动作调用（confirmed_stage 是模型
    不可写的确认标记），产物落盘不经过本端点。写入幂等（同值重复写无副作用），
    返回最新 pipeline_status（phase=新段 + 就绪信号）。
    """
    from app.domains.writing.pipeline import classify_workspace, pipeline_status
    from contracts.writing_artifacts import (
        STAGE_LABELS, load_writing_state, save_writing_state,
    )

    thread_store = get_thread_store()
    workspace = thread_store.get_workspace(user.user_id, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace_path = Path(workspace.workspace_path)
    if payload.stage not in STAGE_LABELS:
        raise HTTPException(status_code=422, detail=f"stage 只能是 {'/'.join(STAGE_LABELS)}")
    if classify_workspace(workspace_path) == "legacy":
        raise HTTPException(status_code=409, detail="旧版工作区只读，不支持切段")
    state = load_writing_state(workspace_path)
    state.confirmed_stage = payload.stage
    state.stage = payload.stage
    save_writing_state(workspace_path, state)
    return pipeline_status(workspace_path)


@router.post("/workspaces/{workspace_id}/writing/stage/advance")
def advance_writing_stage(
    workspace_id: str, payload: WritingStageAdvanceRequest,
    user: CurrentUser = Depends(current_user),
) -> dict:
    """段审确认后的循环切段（自动模式段间推进）——按创作循环推导下一段。

    界面「确认通过」动作调用（人工确认语义，仍走人工段指针唯一通道）：
    一卷卷纲确认 → 章纲；一批章纲确认 → 正文；正文库存尽 → 回章纲；
    本卷章纲齐 → 下一卷卷纲（next_stage_on_confirm，区别于线性五段）。
    trace_id 幂等：同一待审 trace 重复推进 no-op（重试不二次跳段）。
    """
    from app.domains.writing.pipeline import classify_workspace, current_stage, pipeline_status
    from contracts.writing_artifacts import (
        STAGE_LABELS, load_writing_state, next_stage_on_confirm, save_writing_state,
    )

    thread_store = get_thread_store()
    workspace = thread_store.get_workspace(user.user_id, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace_path = Path(workspace.workspace_path)
    if classify_workspace(workspace_path) == "legacy":
        raise HTTPException(status_code=409, detail="旧版工作区只读，不支持切段")
    state = load_writing_state(workspace_path)
    if payload.trace_id and state.last_stage_advance.get("trace_id") == payload.trace_id:
        return pipeline_status(workspace_path)
    current = (
        state.confirmed_stage if state.confirmed_stage in STAGE_LABELS
        else current_stage(workspace_path)
    )
    nxt = next_stage_on_confirm(workspace_path, current)
    state.confirmed_stage = nxt
    state.stage = nxt
    state.last_stage_advance = {
        "trace_id": payload.trace_id, "from": current, "to": nxt,
    }
    save_writing_state(workspace_path, state)
    return pipeline_status(workspace_path)


@router.get("/workspaces/{workspace_id}/writing/mode")
def get_writing_mode(workspace_id: str, user: CurrentUser = Depends(current_user)) -> dict:
    """段级创作模式读取（REQ-20261026-191719 FR-008/FR-011）。

    返回全部五段的模式快照（题材/总纲恒 interactive，DEC-002）与正文批次
    大小；缺省段=interactive（DEC-013：存量书升级后行为零变化）。
    """
    from contracts.writing_artifacts import (
        AUTO_ELIGIBLE_STAGES, STAGE_LABELS,
        load_writing_state, stage_mode, text_batch_size,
    )

    thread_store = get_thread_store()
    workspace = thread_store.get_workspace(user.user_id, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace_path = Path(workspace.workspace_path)
    return {
        "stage_modes": {stage: stage_mode(workspace_path, stage) for stage in STAGE_LABELS},
        "auto_eligible": sorted(AUTO_ELIGIBLE_STAGES),
        "batch_size": text_batch_size(workspace_path),
    }


@router.put("/workspaces/{workspace_id}/writing/mode")
def update_writing_mode(
    workspace_id: str, payload: WritingModeUpdateRequest,
    user: CurrentUser = Depends(current_user),
) -> dict:
    """段级创作模式写入（REQ-20261026-191719 FR-005/FR-008）——模式开关唯一通道。

    只由界面动作调用（模型不可写）；题材/总纲段与非法值 422。批次章数随
    text 段一并回写（DEC-007：审查卡现场指定）。写入在下一停等边界生效
    （DEC-016：正在跑的 run 不被打断，下一轮装配读到新值）。
    """
    from contracts.writing_artifacts import (
        save_writing_state, set_stage_mode, text_batch_size,
    )

    thread_store = get_thread_store()
    workspace = thread_store.get_workspace(user.user_id, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace_path = Path(workspace.workspace_path)
    try:
        set_stage_mode(workspace_path, payload.stage, payload.mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.batch_size is not None and payload.stage == "text":
        state = load_writing_state(workspace_path)
        state.text_batch_size = payload.batch_size
        save_writing_state(workspace_path, state)
    from contracts.writing_artifacts import AUTO_ELIGIBLE_STAGES, STAGE_LABELS, stage_mode
    return {
        "stage_modes": {stage: stage_mode(workspace_path, stage) for stage in STAGE_LABELS},
        "auto_eligible": sorted(AUTO_ELIGIBLE_STAGES),
        "batch_size": text_batch_size(workspace_path),
    }


def _find_manuscript(workspace_path: Path, chapter: int) -> Path | None:
    """定位某章正文文件（允许带章名：第NNN章<章名>.md）。"""
    from contracts.writing_artifacts import TEXT_DIR

    text_dir = workspace_path / TEXT_DIR
    if not text_dir.is_dir():
        return None
    matches = sorted(text_dir.glob(f"第{chapter:03d}章*.md"))
    return matches[0] if matches else None


def _manuscript_status(path: Path) -> str:
    from contracts.writing_artifacts import parse_manuscript_status

    try:
        return parse_manuscript_status(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return "draft"


@router.get("/workspaces/{workspace_id}/manuscript/{chapter}", response_model=ManuscriptContent)
def get_manuscript(workspace_id: str, chapter: int, user: CurrentUser = Depends(current_user)) -> ManuscriptContent:
    """读单章正文（人修编辑器数据源，REQ-20261018-115508 FR-009）。"""
    thread_store = get_thread_store()
    workspace = thread_store.get_workspace(user.user_id, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    path = _find_manuscript(Path(workspace.workspace_path), chapter)
    if path is None:
        return ManuscriptContent(chapter=chapter, markdown="", status="draft", exists=False)
    try:
        markdown = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.exception("正文读取失败: workspace=%s chapter=%s", workspace_id, chapter)
        raise HTTPException(status_code=500, detail="正文读取失败，请重试") from exc
    return ManuscriptContent(
        chapter=chapter, markdown=markdown,
        status=_manuscript_status(path), exists=True,
    )


@router.put("/workspaces/{workspace_id}/manuscript/{chapter}", response_model=ManuscriptContent)
def save_manuscript(
    workspace_id: str, chapter: int, payload: ManuscriptSaveRequest,
    user: CurrentUser = Depends(current_user),
) -> ManuscriptContent:
    """保存人修正文（客户端直写，不经过 agent——FR-009/DEC-007 第三层）。

    finalize=true 时把状态行改写为「人修定稿」（agent 的偏差回写前置条件）。
    """
    from datetime import date

    thread_store = get_thread_store()
    workspace = thread_store.get_workspace(user.user_id, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace_path = Path(workspace.workspace_path)
    path = _find_manuscript(workspace_path, chapter)
    if path is None:
        raise HTTPException(status_code=404, detail=f"第 {chapter} 章正文尚未起草（先由 agent 生成初稿）")
    text = payload.markdown
    if payload.finalize:
        lines = text.splitlines()
        replaced = False
        for i, ln in enumerate(lines[:8]):
            if ln.strip().startswith(">") and "状态" in ln:
                lines[i] = f"> 状态：人修定稿｜{date.today().isoformat()}"
                replaced = True
                break
        if not replaced:
            lines.insert(1, f"> 状态：人修定稿｜{date.today().isoformat()}")
        text = "\n".join(lines) + ("\n" if not text.endswith("\n") else "")
    import uuid as _uuid

    tmp = path.with_suffix(f"{path.suffix}.{_uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.exception("正文保存失败: workspace=%s chapter=%s", workspace_id, chapter)
        raise HTTPException(status_code=500, detail="正文保存失败，请重试") from exc
    return ManuscriptContent(
        chapter=chapter, markdown=text,
        status=_manuscript_status(path), exists=True,
    )


# ════════════════════════════════════════════════════════════
# 产物划词标注（REQ-20261027-213246）——持久便签，agent 定点修改通道
# ════════════════════════════════════════════════════════════


def _annotation_workspace(workspace_id: str, user: CurrentUser) -> Path:
    """取工作区路径并拦截 legacy（DEC-006 非目标：旧工作区只读）。"""
    from app.domains.writing.pipeline import classify_workspace

    thread_store = get_thread_store()
    workspace = thread_store.get_workspace(user.user_id, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace_path = Path(workspace.workspace_path)
    if classify_workspace(workspace_path) == "legacy":
        raise HTTPException(status_code=409, detail="旧版工作区只读，不支持标注")
    return workspace_path


@router.get("/workspaces/{workspace_id}/annotations", response_model=AnnotationsListResponse)
def list_annotations(workspace_id: str, user: CurrentUser = Depends(current_user)) -> AnnotationsListResponse:
    """标注清单全集（读前复验，保证状态与产物现状对齐）。"""
    from app.domains.writing.annotations import load_annotations, reverify_annotations

    workspace_path = _annotation_workspace(workspace_id, user)
    reverify_annotations(workspace_path)
    return AnnotationsListResponse(annotations=load_annotations(workspace_path))


@router.post("/workspaces/{workspace_id}/annotations", response_model=Annotation)
def create_annotation(
    workspace_id: str, payload: AnnotationCreateRequest,
    user: CurrentUser = Depends(current_user),
) -> Annotation:
    """新建标注（FR-001）：服务端校验锚点在产物内唯一命中，歧义/找不到 422。"""
    from app.domains.writing.annotations import create_annotation as _create

    workspace_path = _annotation_workspace(workspace_id, user)
    try:
        return _create(workspace_path, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/workspaces/{workspace_id}/annotations/{annotation_id}", response_model=Annotation)
def update_annotation(
    workspace_id: str, annotation_id: str, payload: AnnotationUpdateRequest,
    user: CurrentUser = Depends(current_user),
) -> Annotation:
    """改意见／重新框选（FR-002）：重框选校验新锚点并复位待处理。"""
    from app.domains.writing.annotations import update_annotation as _update

    workspace_path = _annotation_workspace(workspace_id, user)
    try:
        return _update(workspace_path, annotation_id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/workspaces/{workspace_id}/annotations/{annotation_id}")
def delete_annotation(
    workspace_id: str, annotation_id: str,
    user: CurrentUser = Depends(current_user),
) -> dict[str, str | bool]:
    from app.domains.writing.annotations import delete_annotation as _delete

    workspace_path = _annotation_workspace(workspace_id, user)
    if not _delete(workspace_path, annotation_id):
        raise HTTPException(status_code=404, detail="标注不存在")
    return {"status": "ok", "deleted": annotation_id}


@router.post("/workspaces/{workspace_id}/annotations/clear")
def clear_annotations(
    workspace_id: str, payload: AnnotationClearRequest,
    user: CurrentUser = Depends(current_user),
) -> dict[str, str | int]:
    """一键清空指定终态（已解决/已失效）标注（FR-002）。"""
    from app.domains.writing.annotations import clear_annotations as _clear

    workspace_path = _annotation_workspace(workspace_id, user)
    return {"status": "ok", "deleted": _clear(workspace_path, payload.statuses)}


@router.post("/workspaces/{workspace_id}/annotations/dispatch", response_model=AnnotationDispatchResponse)
def dispatch_annotations(
    workspace_id: str,
    user: CurrentUser = Depends(current_user),
) -> AnnotationDispatchResponse:
    """把全部待处理标注打包成任务消息（FR-004/005）。

    发出前复验剔除失效条目；返回的消息由桌面端经聊天通道（generate/stream，
    复用排队语义 DEC-011）发到当前对话线程，agent 的处理与回复都在聊天面板。
    """
    from app.domains.writing.annotations import dispatch_annotations as _dispatch

    workspace_path = _annotation_workspace(workspace_id, user)
    try:
        message, annotation_ids, invalidated = _dispatch(workspace_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AnnotationDispatchResponse(
        message=message, annotation_ids=annotation_ids, invalidated=invalidated
    )


@router.post("/workspaces/{workspace_id}/artifact/delete", response_model=ArtifactRedoDeleteResponse)
def delete_artifact_for_redo(
    workspace_id: str, payload: ArtifactRedoDeleteRequest,
    user: CurrentUser = Depends(current_user),
) -> ArtifactRedoDeleteResponse:
    """定稿产物删除重做（2026-10-28 修订）——仅最下游的总纲/卷纲。

    界面显式确认后调用：文件移入 .writing/trash/ 暂存、清除该产物标注、
    段指针在下游时回落（总纲→总纲段，卷纲→卷纲段），流水线自然重走。
    """
    from app.domains.writing.pipeline import classify_workspace, pipeline_status
    from app.domains.writing.artifact_redo import delete_artifact_for_redo as _delete

    thread_store = get_thread_store()
    workspace = thread_store.get_workspace(user.user_id, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace_path = Path(workspace.workspace_path)
    if classify_workspace(workspace_path) == "legacy":
        raise HTTPException(status_code=409, detail="旧版工作区只读，不支持删除重做")
    try:
        result = _delete(workspace_path, payload.rel)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    result["pipeline"] = pipeline_status(workspace_path)
    return ArtifactRedoDeleteResponse(**result)


@router.get("/workspaces/{workspace_id}/watch")
async def watch_workspace(workspace_id: str, user: CurrentUser = Depends(current_user)):
    thread_store = get_thread_store()
    workspace = thread_store.get_workspace(user.user_id, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace_path = Path(workspace.workspace_path)
    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="Workspace directory missing")
    return StreamingResponse(
        _workspace_watch_generator(user.user_id, workspace_id, workspace_path),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.delete("/workspaces/{workspace_id}")
async def delete_workspace(workspace_id: str, user: CurrentUser = Depends(current_user)) -> dict[str, str | bool | list[str]]:
    thread_store = get_thread_store()
    try:
        deleted_thread_ids = thread_store.delete_workspace(user.user_id, workspace_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if deleted_thread_ids is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    # T2.9：清理 checkpoint（分库）+ trace
    agent_service = get_agent_service()
    character_service = get_character_service()
    for thread_id in deleted_thread_ids:
        await agent_service.delete_thread_checkpoint(thread_id, owner_id=user.user_id)
        await character_service.delete_thread_checkpoint(thread_id)
    return {"status": "ok", "deleted": workspace_id, "deleted_threads": deleted_thread_ids}
