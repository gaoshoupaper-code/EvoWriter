"""update_foreshadow_table 工具 — 伏笔登记表分区回写（REQ-20261018-115508 FR-005，DEC-006）。

章纲阶段出卡时同步总纲.md 的伏笔登记表（本章埋的登记「待收」，本章收的
改「已收」）。路径与分区逻辑全在本工具内（agent 零路径知识）；调用前置
（章纲阶段）由 stage_gate 硬闸把关。
"""
from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from contracts.writing_artifacts import MASTER_FILENAME

logger = logging.getLogger(__name__)

UPDATE_FORESHADOW_TOOL_DESCRIPTION = """更新总纲的伏笔登记表（仅章纲阶段可用；分区更新，不动总纲其余内容）。

entries 是完整的登记表 markdown 表（含表头行与数据行，列：编号/伏笔/埋于章/收于章/状态），
将整表替换进总纲的「伏笔登记表」小节。表内应保留既有未收条目，只改本次埋/收涉及的行。"""


class UpdateForeshadowInput(BaseModel):
    entries: str = Field(min_length=1, description="完整伏笔登记表 markdown（含表头与全部数据行）")


def replace_section(text: str, heading_keyword: str, new_body: str) -> str | None:
    """把包含 keyword 的标题所辖小节整体替换为 new_body；找不到标题返回 None。"""
    lines = text.splitlines(keepends=True)
    start = None
    level = 0
    for i, ln in enumerate(lines):
        s = ln.lstrip()
        if not s.startswith("#"):
            continue
        lv = len(s) - len(s.lstrip("#"))
        if start is None:
            if heading_keyword in s:
                start, level = i, lv
        elif lv <= level:
            end = i
            head = "".join(lines[:start + 1])
            tail = "".join(lines[end:])
            body = new_body.rstrip() + "\n\n"
            return head + body + tail
    if start is None:
        return None
    head = "".join(lines[:start + 1])
    body = new_body.rstrip() + "\n"
    return head + body


def _atomic_write(path: Path, text: str) -> None:
    import uuid as _uuid

    tmp = path.with_suffix(f"{path.suffix}.{_uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def build_update_foreshadow_tool(workspace_root: Path) -> StructuredTool:
    def _update(entries: str) -> str:
        if "|编号|" not in entries.replace(" ", "") and "编号" not in entries:
            return "[update_foreshadow_table 失败] entries 必须是含「编号/伏笔/埋于章/收于章/状态」列的完整 markdown 表。"
        path = workspace_root / MASTER_FILENAME
        if not path.is_file():
            return "[update_foreshadow_table 失败] 总纲尚未落盘——伏笔登记表随总纲产生，本工具仅在章纲阶段更新它。"
        try:
            current = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return f"[update_foreshadow_table 失败] 读取失败：{type(exc).__name__}"
        updated = replace_section(current, "伏笔登记表", entries)
        if updated is None:
            return "[update_foreshadow_table 失败] 总纲中找不到「伏笔登记表」小节——先在总纲落盘时建立该小节。"
        try:
            _atomic_write(path, updated)
        except OSError as exc:
            return f"[update_foreshadow_table 失败] 写入失败：{type(exc).__name__}"
        return "伏笔登记表已更新（分区替换，总纲其余内容不变）。"

    return StructuredTool.from_function(
        name="update_foreshadow_table",
        description=UPDATE_FORESHADOW_TOOL_DESCRIPTION,
        func=_update,
        args_schema=UpdateForeshadowInput,
        infer_schema=False,
    )


def build(abc):
    return build_update_foreshadow_tool(abc.workspace_path)


__all__ = [
    "UPDATE_FORESHADOW_TOOL_DESCRIPTION",
    "UpdateForeshadowInput",
    "build",
    "build_update_foreshadow_tool",
    "replace_section",
]
