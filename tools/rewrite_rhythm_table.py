"""rewrite_rhythm_table 工具 — 卷纲剧情推进表分区回写。

摊章弹性回写（技能纪律：数量是预估、纪律为准，摊完回写推进表）。分区替换
目标卷卷纲的推进表小节；前置（章纲阶段）由 stage_gate 把关。
锚点兼容新旧两种节名（REQ-20261030-200030 DEC-006）：新格式「剧情推进表」、
旧格式「爽点节奏表」均可整节替换，旧书不迁移。
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from contracts.writing_artifacts import KIND_VOLUME, artifact_relpath
from contracts.writing_artifacts import VOLUME_DIR

logger = logging.getLogger(__name__)

REWRITE_RHYTHM_TOOL_DESCRIPTION = """回写某卷卷纲的剧情推进表（仅章纲阶段可用；分区更新，不动卷纲其余内容）。

摊章区间因弹性变动（某段占 5 章而非 4 章等）时，用 table 提交该卷完整的
新推进表（逐章排，剧情＋爽点＋压抑双供给），整表替换卷纲的「剧情推进表」
小节（旧书「爽点节奏表」节名同样支持）并注明摊章日期。"""


class RewriteRhythmInput(BaseModel):
    volume: int = Field(ge=1, description="卷号")
    table: str = Field(min_length=1, description="该卷完整的新推进表 markdown（含摊章日期注记）")


def _atomic_write(path: Path, text: str) -> None:
    import uuid as _uuid

    tmp = path.with_suffix(f"{path.suffix}.{_uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def build_rewrite_rhythm_tool(workspace_root: Path, *, capture=None) -> StructuredTool:
    from .update_foreshadow_table import replace_section

    def _rewrite(volume: int, table: str) -> str:
        rel = artifact_relpath(KIND_VOLUME, volume=volume)
        path = workspace_root / rel
        if not path.is_file():
            return f"[rewrite_rhythm_table 失败] 第 {volume} 卷卷纲尚未落盘（{VOLUME_DIR} 下无该卷）。"
        try:
            current = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return f"[rewrite_rhythm_table 失败] 读取失败：{type(exc).__name__}"
        # 新节名「剧情推进表」优先；旧书按「节奏」锚定「爽点节奏表」，不迁移。
        updated = replace_section(current, "剧情推进", table)
        if updated is None:
            updated = replace_section(current, "节奏", table)
        if updated is None:
            return f"[rewrite_rhythm_table 失败] 第 {volume} 卷卷纲中找不到「剧情推进表／节奏表」小节。"
        # 章号范围行是切段状态机的消费不变量（缺/重都会让 ranges 失配），
        # 只守这一行，不恢复内容校验。
        if len(re.findall(r"本卷章号范围", updated)) != 1:
            return "[rewrite_rhythm_table 失败] 提交的表须且只须包含一行「本卷章号范围：起始章-结束章」。"
        try:
            _atomic_write(path, updated)
        except OSError as exc:
            return f"[rewrite_rhythm_table 失败] 写入失败：{type(exc).__name__}"
        if capture is not None:
            try:
                capture(rel, updated)
            except Exception:  # noqa: BLE001 — 取证失败不影响已完成的回写
                logger.warning("rewrite_rhythm_table 取证失败（已降级）", exc_info=True)
        return f"第 {volume} 卷推进表已回写（分区替换，卷纲其余内容不变）。"

    return StructuredTool.from_function(
        name="rewrite_rhythm_table",
        description=REWRITE_RHYTHM_TOOL_DESCRIPTION,
        func=_rewrite,
        args_schema=RewriteRhythmInput,
        infer_schema=False,
    )


def build(abc):
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
        capture=capture if callback is not None else None,
    )


__all__ = [
    "REWRITE_RHYTHM_TOOL_DESCRIPTION",
    "RewriteRhythmInput",
    "build",
    "build_rewrite_rhythm_tool",
]
