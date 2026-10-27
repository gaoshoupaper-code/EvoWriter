"""定稿产物删除重做（REQ-20261027-213246 修订 2026-10-28）。

「删除重做」仅对处于**最下游**的总纲/卷纲开放——它是流水线最后产出的
产物，后面没有任何下游文件，删掉不牵连已确认内容：

  - 总纲.md：卷纲/章纲/正文 目录全空
  - 卷纲/第N卷.md：N 为现有最大卷号，且 章纲/正文 目录全空

删除动作（用户显式发起、界面二次确认）：
  1. 文件移入 .writing/trash/<原文件名>.<时间戳>.md（误删可人工找回，
     不在产物树内、不触发 outline watch 分类）；
  2. 该产物的标注（含全部状态）一并清除——文件都将重做，旧标注无意义
     （区别于 DEC-009 的复验不删语义：这里是用户显式删除动作）；
  3. 人工段指针只在位于下游时回落（总纲→outline，卷纲→volume），
     指针在上游时不动（不借删除往前推段）。
"""
from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

from contracts.writing_artifacts import (
    CHAPTER_OUTLINE_DIR,
    MASTER_FILENAME,
    STAGE_CHAPTER,
    STAGE_LABELS,
    STAGE_OUTLINE,
    STAGE_TEXT,
    STAGE_THEME,
    STAGE_VOLUME,
    TEXT_DIR,
    VOLUME_DIR,
    VOLUME_FILE_PATTERN,
    load_writing_state,
    save_writing_state,
)

logger = logging.getLogger(__name__)

_VOLUME_REL_RE = re.compile(r"^卷纲/第(\d+)卷\.md$")

# 段先后序（指针回落判断用：只在指针位于目标段下游时拉回）
_STAGE_ORDER = {STAGE_THEME: 0, STAGE_OUTLINE: 1, STAGE_VOLUME: 2, STAGE_CHAPTER: 3, STAGE_TEXT: 4}

_TRASH_DIR = ".writing/trash"


def _dir_has_md(workspace: Path, rel_dir: str) -> bool:
    d = workspace / rel_dir
    return d.is_dir() and any(f.suffix == ".md" for f in d.iterdir())


def redo_target_stage(rel: str) -> str | None:
    """可删除重做的产物 → 回落段；范围外返回 None。"""
    if rel == MASTER_FILENAME:
        return STAGE_OUTLINE
    if _VOLUME_REL_RE.match(rel):
        return STAGE_VOLUME
    return None


def redo_eligibility(workspace: Path, rel: str) -> tuple[bool, str]:
    """删除重做资格（最下游判定）。返回 (可删, 不可删原因)。"""
    if rel == MASTER_FILENAME:
        downstream = [d for d in (VOLUME_DIR, CHAPTER_OUTLINE_DIR, TEXT_DIR) if _dir_has_md(workspace, d)]
        if downstream:
            return False, "总纲不是最下游：已存在下游产物（" + "、".join(downstream) + "）。先在界面回退处理下游，才能删除重做总纲。"
        return True, ""
    m = _VOLUME_REL_RE.match(rel)
    if not m:
        return False, "删除重做只支持总纲与卷纲。"
    vol = int(m.group(1))
    vol_dir = workspace / VOLUME_DIR
    vols = sorted(
        int(VOLUME_FILE_PATTERN.match(f.name).group(1))
        for f in vol_dir.iterdir()
        if f.is_file() and VOLUME_FILE_PATTERN.match(f.name)
    ) if vol_dir.is_dir() else []
    if vols and vol != vols[-1]:
        return False, f"第 {vol} 卷不是最下游：已有更靠后的卷纲（第 {vols[-1]} 卷）。只能删除重做最新一卷。"
    downstream = [d for d in (CHAPTER_OUTLINE_DIR, TEXT_DIR) if _dir_has_md(workspace, d)]
    if downstream:
        return False, "该卷不是最下游：已存在下游产物（" + "、".join(downstream) + "）。先在界面回退处理下游，才能删除重做。"
    return True, ""


def delete_artifact_for_redo(workspace: Path, rel: str) -> dict:
    """执行删除重做。资格不符/文件不存在抛 ValueError（端点转 4xx）。"""
    target_stage = redo_target_stage(rel)
    if target_stage is None:
        raise ValueError("删除重做只支持总纲与卷纲。")
    src = workspace / rel
    if not src.is_file():
        raise ValueError(f"产物文件不存在：{rel}")
    ok, reason = redo_eligibility(workspace, rel)
    if not ok:
        raise ValueError(reason)

    # 1. 移入回收暂存（同目录结构展平为文件名前缀，避免建目录树）
    trash_dir = workspace / _TRASH_DIR
    trash_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    trash_rel = f"{_TRASH_DIR}/{rel.replace('/', '__')}.{stamp}.md"
    shutil.move(str(src), str(workspace / trash_rel))
    logger.info("删除重做：%s -> %s", rel, trash_rel)

    # 2. 清掉该产物的标注（用户显式删除，区别于复验不删）
    removed_annotations = 0
    try:
        from app.domains.writing.annotations import load_annotations, save_annotations

        anns = load_annotations(workspace)
        kept = [a for a in anns if a.artifact_rel != rel]
        removed_annotations = len(anns) - len(kept)
        if removed_annotations:
            save_annotations(workspace, kept)
    except Exception:  # noqa: BLE001 — 标注清理失败不阻断删除主流程
        logger.warning("删除重做时清理标注失败: %s", rel, exc_info=True)

    # 3. 段指针回落：仅在指针位于目标段下游时拉回（不往前推）
    state = load_writing_state(workspace)
    current = state.confirmed_stage if state.confirmed_stage in _STAGE_ORDER else None
    if current is not None and _STAGE_ORDER[current] > _STAGE_ORDER[target_stage]:
        state.confirmed_stage = target_stage
        state.stage = target_stage
        save_writing_state(workspace, state)

    return {
        "status": "ok",
        "deleted": rel,
        "trash_rel": trash_rel,
        "removed_annotations": removed_annotations,
        "stage_reset_to": target_stage if current and _STAGE_ORDER[current] > _STAGE_ORDER[target_stage] else None,
        "stage_label": STAGE_LABELS.get(target_stage, target_stage),
    }
