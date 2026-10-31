"""list_characters 工具 — 在册人物卡清单（REQ-20261031-183257 FR-004/DEC-011c）。

卷纲段第 0 步建配角卡前，需要知道已有哪些卡（防重复建卡、同名冲突）——
写作 agent 此前没有任何枚举产物的工具。本工具只列人物目录，返回每个人名
＋卡片首行标题（档位信息活在标题行里，如「人物卡 · 重要配角 · 老陈」）。

只读不写，无参数；人物目录不存在时提示无卡（老作品即此形态，DEC-017）。
"""
from __future__ import annotations

from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from contracts.writing_artifacts import (
    CHARACTER_FILE_PATTERN,
    CHARACTER_NAME_PATTERN,
)


LIST_CHARACTERS_TOOL_DESCRIPTION = """列出本书全部在册人物卡（人物/ 目录）。

返回每个人名＋卡片首行标题。建配角卡前必查（防重复建卡、同名冲突）；
判断本书是否启用人物卡机制：清单里有「主角」即启用。"""


class ListCharactersInput(BaseModel):
    pass  # 无参数


def list_characters() -> str:  # pragma: no cover - 由 build 闭包实现
    raise NotImplementedError("由 build(abc) 闭包实现（需要 workspace）")


def build_list_characters_tool(workspace_root: Path) -> StructuredTool:
    """构建在册人物卡清单工具。"""

    def _first_line(text: str) -> str:
        for ln in text.splitlines():
            if ln.strip():
                return ln.strip().lstrip("# ").strip()
        return ""

    def _list() -> str:
        directory = workspace_root / "人物"
        if not directory.is_dir():
            return "[list_characters] 本书还没有人物卡（老书未启用人物卡机制，或总纲段未建卡）。"
        rows: list[str] = []
        for f in sorted(directory.iterdir()):
            m = CHARACTER_FILE_PATTERN.match(f.name)
            if not (m and CHARACTER_NAME_PATTERN.match(m.group(1))):
                continue
            try:
                head = _first_line(f.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                head = ""
            rows.append(f"- {m.group(1)}：{head}" if head else f"- {m.group(1)}")
        if not rows:
            return "[list_characters] 本书还没有人物卡（老书未启用人物卡机制，或总纲段未建卡）。"
        return "在册人物卡：\n" + "\n".join(rows)

    return StructuredTool.from_function(
        name="list_characters",
        description=LIST_CHARACTERS_TOOL_DESCRIPTION,
        func=_list,
        args_schema=ListCharactersInput,
        infer_schema=False,
    )


def build(abc):
    """架构清单挂载钩子。"""
    return build_list_characters_tool(abc.workspace_path)


__all__ = [
    "LIST_CHARACTERS_TOOL_DESCRIPTION",
    "ListCharactersInput",
    "build",
    "build_list_characters_tool",
]
