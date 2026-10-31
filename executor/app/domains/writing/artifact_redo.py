"""定稿产物删除重做（REQ-20261030-211446 全量开放：六类产物）。

六类产物（主题/总纲/卷纲/章纲/文风/正文）全部开放「删除重做」，资格
「从下往上、不级联」（DEC-001）：被删产物的直接下游必须已全部删除——

  - 主题.md：总纲.md 不存在
  - 总纲.md：卷纲/章纲/正文 目录无任何 .md
  - 卷纲/第N卷.md：本卷章号范围内无章纲；范围解析失败兜底为章纲目录
    全空（fail-safe，宁可拒删不可误放行）
  - 章纲/第N章.md：第 N 章正文不存在
  - 正文/第N章.md：无下游，随时可删
  - 文风.md：特例不检查下游（DEC-002——重写文风说明书不等于重写正文）

任意位置可删（DEC-003）：中间卷/章同样可删，编号留空档、既有文件不
重排，进度推导按「缺失即待补」识别。

删除动作（用户显式发起、界面二次确认）：
  1. 文件移入 .writing/trash/<原文件名>.<时间戳含微秒>.md（回收站端点
     可列表/恢复；误删自救见 FR-006）；
  2. 该产物的标注（含全部状态）一并清除——文件将重做，旧标注无意义
     （区别于 DEC-009 的复验不删语义：这里是用户显式删除动作）；
  3. 人工段指针只在位于目标段下游时回落（正文目标段即 text，指针
     不可能在更下游，天然不回落），不借删除往前推段。
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

from contracts.writing_artifacts import (
    CHAPTER_OUTLINE_DIR,
    CHAPTER_OUTLINE_PATTERN,
    KIND_CHAPTER,
    KIND_CHARACTER,
    KIND_OUTLINE,
    KIND_STYLE,
    KIND_TEXT,
    KIND_THEME,
    KIND_VOLUME,
    MASTER_FILENAME,
    STAGE_CHAPTER,
    STAGE_LABELS,
    STAGE_OUTLINE,
    STAGE_TEXT,
    STAGE_THEME,
    STAGE_VOLUME,
    TEXT_DIR,
    TEXT_FILE_PATTERN,
    VOLUME_DIR,
    load_writing_state,
    parse_relpath,
    parse_volume_chapter_range,
    save_writing_state,
)

logger = logging.getLogger(__name__)

# 段先后序（指针回落判断用：只在指针位于目标段下游时拉回）
_STAGE_ORDER = {STAGE_THEME: 0, STAGE_OUTLINE: 1, STAGE_VOLUME: 2, STAGE_CHAPTER: 3, STAGE_TEXT: 4}

_KIND_STAGE = {
    KIND_THEME: STAGE_THEME,
    KIND_OUTLINE: STAGE_OUTLINE,
    KIND_VOLUME: STAGE_VOLUME,
    KIND_CHAPTER: STAGE_CHAPTER,
    KIND_STYLE: STAGE_CHAPTER,
    KIND_TEXT: STAGE_TEXT,
    # 人物卡是跨段支线产物：挂最高段使删除永不回落段指针（删卡不动写作流程）
    KIND_CHARACTER: STAGE_TEXT,
}

_TRASH_DIR = ".writing/trash"


def _dir_has_md(workspace: Path, rel_dir: str) -> bool:
    d = workspace / rel_dir
    return d.is_dir() and any(f.suffix == ".md" for f in d.iterdir())


def _inside_workspace(workspace: Path, target: Path) -> bool:
    """路径边界断言（防御纵深）：resolve 后必须仍在 workspace 内。

    正则层已堵死分隔符穿越（TEXT_FILE_PATTERN 标题组收紧）；此断言兜住
    任何未来模式放宽或符号链接导致的越界，模式与 internal.py 读端一致。
    """
    try:
        target.resolve().relative_to(workspace.resolve())
    except ValueError:
        return False
    return True


def _chapter_numbers(workspace: Path, rel_dir: str, pattern) -> list[int]:
    d = workspace / rel_dir
    if not d.is_dir():
        return []
    return sorted(
        int(m.group(1)) for f in d.iterdir() if f.is_file() and (m := pattern.match(f.name))
    )


def redo_target_stage(rel: str) -> str | None:
    """可删除重做的产物 → 回落段；范围外返回 None。"""
    ref = parse_relpath(rel)
    if ref is None:
        return None
    return _KIND_STAGE.get(ref.kind)


def redo_eligibility(workspace: Path, rel: str) -> tuple[bool, str]:
    """删除重做资格（从下往上判定矩阵，FR-002）。返回 (可删, 不可删原因)。"""
    ref = parse_relpath(rel)
    if ref is None:
        return False, "删除重做只支持五段产物文件（主题/总纲/卷纲/章纲/文风/正文）。"

    if ref.kind == KIND_THEME:
        if (workspace / MASTER_FILENAME).is_file():
            return False, "主题不是最下游：总纲已存在。先删除重做总纲，才能删除重做主题。"
        return True, ""

    if ref.kind == KIND_OUTLINE:
        downstream = [d for d in (VOLUME_DIR, CHAPTER_OUTLINE_DIR, TEXT_DIR) if _dir_has_md(workspace, d)]
        if downstream:
            return False, "总纲不是最下游：已存在下游产物（" + "、".join(downstream) + "）。先在界面回退处理下游，才能删除重做总纲。"
        return True, ""

    if ref.kind == KIND_VOLUME:
        vol_file = workspace / rel
        rng = None
        if vol_file.is_file():
            rng = parse_volume_chapter_range(vol_file.read_text(encoding="utf-8", errors="replace"))
        chapter_nums = _chapter_numbers(workspace, CHAPTER_OUTLINE_DIR, CHAPTER_OUTLINE_PATTERN)
        if rng is None:
            if chapter_nums:
                return False, "无法解析该卷的「本卷章号范围」，且章纲目录非空。为避免误删，要求章纲全空才可删除该卷。"
            return True, ""
        hit = [c for c in chapter_nums if rng[0] <= c <= rng[1]]
        if hit:
            lo, hi = min(hit), max(hit)
            span = f"第 {lo} 章" if lo == hi else f"第 {lo}-{hi} 章"
            return False, f"该卷不是最下游：本卷范围内仍有章纲（{span}）。先删除重做这些章纲，才能删除重做该卷。"
        return True, ""

    if ref.kind == KIND_CHAPTER:
        if ref.chapter in _chapter_numbers(workspace, TEXT_DIR, TEXT_FILE_PATTERN):
            return False, f"第 {ref.chapter:03d} 章章纲不是最下游：该章正文仍在。先删除重做该章正文，才能删除重做章纲。"
        return True, ""

    # 正文（无下游）与文风（DEC-002 特例）：随时可删
    if ref.kind in (KIND_TEXT, KIND_STYLE):
        return True, ""

    # 人物卡（跨段支线产物，叶级）：随时可删；删除主角卡等于本书退回
    # 未启用人物卡状态（分流判定按主角卡存在与否），由用户显式决定。
    if ref.kind == KIND_CHARACTER:
        return True, ""

    return False, "删除重做只支持五段产物文件（主题/总纲/卷纲/章纲/文风/正文）。"


def delete_artifact_for_redo(workspace: Path, rel: str) -> dict:
    """执行删除重做。资格不符/文件不存在抛 ValueError（端点转 4xx）。"""
    target_stage = redo_target_stage(rel)
    if target_stage is None:
        raise ValueError("删除重做只支持五段产物文件（主题/总纲/卷纲/章纲/文风/正文）。")
    src = workspace / rel
    if not _inside_workspace(workspace, src):
        raise ValueError("非法的产物路径。")
    if not src.is_file():
        raise ValueError(f"产物文件不存在：{rel}")
    ok, reason = redo_eligibility(workspace, rel)
    if not ok:
        raise ValueError(reason)
    if "__" in rel:
        # 回收站命名用 __ 承载 /：文件名本身含 __ 时暂存名无法无损还原，
        # 宁拒删（fail-safe，与卷范围解析失败兜底同向）。
        raise ValueError("该产物文件名含「__」，删除后回收站无法还原原路径，已拒绝删除。")

    # 1. 移入回收暂存（同目录结构展平为文件名前缀，避免建目录树；
    #    时间戳含微秒，同秒连续删除同名产物也不互相覆盖）
    trash_dir = workspace / _TRASH_DIR
    trash_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
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
