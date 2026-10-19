"""prompt_assets_api —— 提示词资产端点（工作区草稿审核 / 编辑 / 一键发版）。

桌面端 Harness 要素页 Prompt Tab 的编辑后端。数据面是 harness 工作目录
（work_dir，下一版本的暂存区）：prompts/*.md 全量可见，保存即改草稿，
发版走既有管线（commit_and_push → Platform probe 门禁 → promote → executor 热加载）。

端点（/api/prompts 前缀；公网请求经全局 SSOAuthMiddleware 鉴权）：
  GET  /prompts/workdir   工作区提示词草稿清单（文件 + 标题 + 全文 + 脏标记 + HEAD）
  PUT  /prompts/workdir   保存单个提示词文件（仅限既有 prompts/*.md，进化 round 互斥）
  POST /prompts/release   草稿发版：commit + probe + 晋级生产，返回新版本号

与要素视图（elements_api，只读 git commit 快照）的分工：
本模块读写工作区活文件——「看历史版本」走 elements，「改并上线」走这里。
工作区在每次 promote 后与生产 HEAD 一致（commit_and_push 从工作区创建 commit）。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.git_ops import (
    commit_and_push,
    current_commit,
    has_changes,
    work_dir,
)
from app.versioning.elements_api import _prompt_title
from app.versioning.release_gate import ReleasePromoteError, promote_release

logger = logging.getLogger("evolution.prompt_assets")

router = APIRouter(prefix="/prompts", tags=["prompts"])

# 合法草稿路径：prompts/<安全字符>.md（拒绝路径穿越 / 子目录 / 其他目录）
_PROMPT_PATH_RE = re.compile(r"^prompts/[A-Za-z0-9_\-\u4e00-\u9fff]+\.md$")


class PromptSaveRequest(BaseModel):
    path: str = Field(description="相对包根的提示词路径，如 prompts/author_base.md")
    content: str = Field(description="新全文（整体覆盖）")


class PromptReleaseRequest(BaseModel):
    note: str = Field(default="", description="版本说明（空则用默认文案）")


def _reject_if_evolve_round_running(action: str) -> None:
    """与进化会话互斥：任一进化 round 在跑时拒绝改草稿/发版（409）。

    进化 round 与人工编辑共用 harness 工作目录，并发写会互相覆盖
    （镜像 evolve/api._reject_if_round_running 的并发保护语义，全局化）。
    """
    from app.evolve.api import _running_tasks

    running = [sid for sid, task in _running_tasks.items() if not task.done()]
    if running:
        raise _conflict(
            f"进化 session {running[0]} 有未完成 round，无法{action}；等 round 结束或先停止。"
        )


def _conflict(detail: str):
    from fastapi import HTTPException

    return HTTPException(status_code=409, detail=detail)


def _prompt_dir() -> Path:
    directory = work_dir() / "prompts"
    if not directory.is_dir():
        raise _conflict("harness 工作区无 prompts/ 目录（工作区未初始化）")
    return directory


@router.get("/workdir")
def list_workdir_prompts() -> dict[str, Any]:
    """工作区提示词草稿全量清单 + 脏标记。

    dirty 只看 prompts/ 下的改动（本页只编辑提示词）；HEAD 供前端标注基线。
    """
    directory = _prompt_dir()
    files: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.md")):
        try:
            body = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("草稿读取失败: %s (%s)", path.name, exc)
            continue
        rel = f"prompts/{path.name}"
        files.append({
            "path": rel,
            "title": _prompt_title(body, rel),
            "body": body,
        })
    dirty = _prompts_dirty()
    return {
        "files": files,
        "dirty": dirty,
        "head": current_commit(),
    }


def _prompts_dirty() -> bool:
    """工作区 prompts/ 下是否有未提交改动（git status --porcelain -- prompts）。"""
    from app.core.git_ops import _git

    try:
        out = _git(["status", "--porcelain", "--", "prompts"], work_dir())
    except Exception:  # noqa: BLE001 — git 异常按「有改动」处理，宁可不发版
        logger.warning("git status 失败，dirty 按 True 处理", exc_info=True)
        return True
    return bool(out.strip())


@router.put("/workdir")
def save_workdir_prompt(payload: PromptSaveRequest) -> dict[str, Any]:
    """保存单个提示词草稿（整体覆盖写，UTF-8）。

    只允许覆盖 prompts/ 下既有的 .md——新建文件走正常 harness 开发流程
    （新文件要同步清单/包声明，不适合从这页创建）。
    """
    _reject_if_evolve_round_running("保存提示草稿")
    if not _PROMPT_PATH_RE.match(payload.path):
        raise _conflict(f"非法提示词路径: {payload.path}（仅限 prompts/<文件名>.md）")
    target = work_dir() / payload.path
    if not target.is_file():
        raise _conflict(f"{payload.path} 不存在于工作区（此页不新建提示词文件）")
    target.write_text(payload.content, encoding="utf-8")
    logger.info("提示词草稿已保存: %s (%d 字符)", payload.path, len(payload.content))
    return {"ok": True, "path": payload.path, "dirty": _prompts_dirty()}


@router.post("/release")
def release_prompt_draft(payload: PromptReleaseRequest) -> dict[str, Any]:
    """草稿发版：工作区 commit → Platform 门禁 probe → 晋级生产 → executor 热加载。

    无改动时 409（避免空 commit 版本）；probe/晋升失败抛 502/409，账本不动。
    """
    _reject_if_evolve_round_running("发版")
    if not has_changes():
        raise _conflict("工作区无未提交改动，无需发版")
    note = payload.note.strip() or "提示词草稿发版（Harness 要素页）"
    commit = commit_and_push(f"prompts: {note}")
    try:
        promoted = promote_release(commit, version_note=note)
    except (ReleasePromoteError, ValueError) as exc:
        raise _conflict(f"发版失败（工作区已提交为 {commit[:9]}，账本未动）: {exc}") from exc
    # 镜像备份（bare → 外部 git 托管分支）：软失败不阻断，与进化发版链路同口径
    try:
        from app.core.git_ops import push_mirror

        push_mirror()
    except Exception:  # noqa: BLE001 — 备份失败不影响发版结果，下次发版补推
        logger.exception("harness 镜像推送失败（不影响发版）")
    logger.info("提示词草稿已发版: v%s @ %s", promoted.version, promoted.commit[:9])
    return {"version": promoted.version, "commit": promoted.commit}
