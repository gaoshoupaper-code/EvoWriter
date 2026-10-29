"""rewrite_rhythm_table 工具 — 卷纲节奏表分区回写（REQ-20261018-115508 FR-005，DEC-006）。

摊章弹性回写（技能纪律：数量是预估、纪律为准，摊完回写节奏表）。分区替换
目标卷卷纲的「节奏」小节；前置（章纲阶段）由 stage_gate 把关。
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from contracts.writing_artifacts import KIND_VOLUME, artifact_relpath
from contracts.writing_artifacts import VOLUME_DIR

logger = logging.getLogger(__name__)

REWRITE_RHYTHM_TOOL_DESCRIPTION = """回写某卷卷纲的爽点节奏表（仅章纲阶段可用；分区更新，不动卷纲其余内容）。

摊章区间因弹性变动（某段占 5 章而非 4 章等）时，用 table 提交该卷完整的
新节奏表（逐章排，爽点＋压抑双供给），整表替换卷纲的「节奏」小节并注明摊章日期。"""


class RewriteRhythmInput(BaseModel):
    volume: int = Field(ge=1, description="卷号")
    table: str = Field(min_length=1, description="该卷完整的新节奏表 markdown（含摊章日期注记）")


def _atomic_write(path: Path, text: str) -> None:
    import uuid as _uuid

    tmp = path.with_suffix(f"{path.suffix}.{_uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _rhythm_edit_scope(text: str) -> dict[str, str]:
    """整卷检查，但自动修复只获准修改节奏标题所辖正文。"""
    lines = text.splitlines(keepends=True)
    start, level = None, 0
    for index, line in enumerate(lines):
        heading = line.lstrip()
        if not heading.startswith("#"):
            continue
        depth = len(heading) - len(heading.lstrip("#"))
        if start is None and "节奏" in heading:
            start, level = index, depth
        elif start is not None and depth <= level:
            return {"prefix": "".join(lines[:start + 1]), "suffix": "".join(lines[index:])}
    if start is None:
        raise ValueError("卷纲中找不到节奏小节")
    return {"prefix": "".join(lines[:start + 1]), "suffix": ""}


def build_rewrite_rhythm_tool(workspace_root: Path, *, volume_guard=None,
                              capture=None) -> StructuredTool:
    from .update_foreshadow_table import replace_section
    from .save_artifact import _guard_volume_write, _volume_write_message

    def _rewrite(volume: int, table: str) -> str:
        rel = artifact_relpath(KIND_VOLUME, volume=volume)
        path = workspace_root / rel
        if not path.is_file():
            return f"[rewrite_rhythm_table 失败] 第 {volume} 卷卷纲尚未落盘（{VOLUME_DIR} 下无该卷）。"
        try:
            current = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return f"[rewrite_rhythm_table 失败] 读取失败：{type(exc).__name__}"
        updated = replace_section(current, "节奏", table)
        if updated is None:
            return f"[rewrite_rhythm_table 失败] 第 {volume} 卷卷纲中找不到「节奏」小节。"
        guarded = _guard_volume_write(
            volume_guard, volume=volume, base_text=current,
            candidate_text=updated, source="rewrite", capture=capture,
            edit_scope=_rhythm_edit_scope(current),
        )
        if guarded is not None:
            return _volume_write_message(guarded, volume, rewrite=True)
        try:
            _atomic_write(path, updated)
        except OSError as exc:
            return f"[rewrite_rhythm_table 失败] 写入失败：{type(exc).__name__}"
        return f"第 {volume} 卷节奏表已回写（分区替换，卷纲其余内容不变）。"

    return StructuredTool.from_function(
        name="rewrite_rhythm_table",
        description=REWRITE_RHYTHM_TOOL_DESCRIPTION,
        func=_rewrite,
        args_schema=RewriteRhythmInput,
        infer_schema=False,
    )


def build(abc):
    from .save_artifact import _MISSING_VOLUME_GUARD

    callback = getattr(abc, "artifact_callback", None)

    def capture(rel: str, text: str) -> None:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        callback({
            "file_path": rel, "content": text, "fingerprint": digest,
            "tool": "rewrite_rhythm_table",
            "tool_call_id": f"rewrite_rhythm_table:{digest[:16]}",
            "agent_name": getattr(abc, "trace_name", "author-subagent"),
        })

    return build_rewrite_rhythm_tool(
        abc.workspace_path,
        volume_guard=getattr(abc.ctx, "volume_write_guard", _MISSING_VOLUME_GUARD),
        capture=capture if callback is not None else None,
    )


__all__ = [
    "REWRITE_RHYTHM_TOOL_DESCRIPTION",
    "RewriteRhythmInput",
    "build",
    "build_rewrite_rhythm_tool",
]
