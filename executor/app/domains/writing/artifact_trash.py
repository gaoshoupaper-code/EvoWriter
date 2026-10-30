"""回收站（.writing/trash）列表与恢复（REQ-20261030-211446 FR-006）。

删除重做把产物移入 ``.writing/trash/<原名 / 换 __>.<时间戳>.md``；本模块
提供两个反向操作：

  - 列表：解析暂存文件名还原「原名＋类型＋删除时间」，按删除时间倒序；
  - 恢复：把文件移回原相对路径。原位已被新文件占用时**不得覆盖**
    （DEC-005：不覆盖语义）；恢复不动段指针（目录即进度自然重算）、
    不恢复已清除的标注（单向）。

fail-safe：文件名解析不出五段产物原路径（含 ``__`` 歧义、非产物名）的
暂存条目不列出、不可恢复——宁可少恢复，不恢复到错误位置。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from contracts.writing_artifacts import (
    KIND_CHAPTER,
    KIND_OUTLINE,
    KIND_STYLE,
    KIND_TEXT,
    KIND_THEME,
    KIND_VOLUME,
    parse_relpath,
)

logger = logging.getLogger(__name__)

TRASH_DIR = ".writing/trash"

# 暂存文件名：<原 rel（/ → __）>.<YYYYMMDD-HHMMSS(-ffffff)>.md
# （-ffffff 微秒段为 2026-10-30 起新增，兼容无微秒的历史文件）
_ENTRY_RE = re.compile(r"^(?P<orig>.+\.md)\.(?P<stamp>\d{8}-\d{6}(?:-\d{6})?)\.md$")

# trash_rel 本体必须落在回收站目录内、单层文件名（防路径穿越；
# 文件名段同时排除反斜杠，堵 Windows 语义下的 ..\..\ 变体）
_TRASH_REL_RE = re.compile(r"^" + re.escape(TRASH_DIR) + r"/[^/\\]+\.md$")

_KIND_LABELS = {
    KIND_THEME: "题材",
    KIND_OUTLINE: "总纲",
    KIND_VOLUME: "卷纲",
    KIND_CHAPTER: "章纲",
    KIND_STYLE: "文风",
    KIND_TEXT: "正文",
}


def _parse_entry(name: str) -> tuple[str, str] | None:
    """暂存文件名 → (原 rel, 时间戳)；解析不出返回 None。"""
    m = _ENTRY_RE.match(name)
    if not m:
        return None
    return m.group("orig").replace("__", "/"), m.group("stamp")


def _stamp_display(stamp: str) -> str:
    try:
        return datetime.strptime(stamp[:15], "%Y%m%d-%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return stamp


def list_trash(workspace: Path) -> list[dict]:
    """回收站条目（按删除时间倒序）；解析失败的条目跳过（fail-safe）。"""
    d = Path(workspace) / TRASH_DIR
    if not d.is_dir():
        return []
    items: list[tuple[str, dict]] = []
    for f in d.iterdir():
        if not f.is_file():
            continue
        parsed = _parse_entry(f.name)
        if parsed is None:
            continue
        orig, stamp = parsed
        ref = parse_relpath(orig)
        if ref is None:
            continue
        items.append((stamp, {
            "trash_rel": f"{TRASH_DIR}/{f.name}",
            "original_rel": orig,
            "kind_label": _KIND_LABELS.get(ref.kind, ref.kind),
            "deleted_at": _stamp_display(stamp),
        }))
    items.sort(key=lambda t: t[0], reverse=True)
    return [entry for _, entry in items]


def restore_trash_item(workspace: Path, trash_rel: str) -> dict:
    """恢复暂存文件回原位。

    记录不存在抛 LookupError（端点转 404）；路径非法/原位被占抛
    ValueError（端点转 409，原位被占不覆盖）。
    """
    if not _TRASH_REL_RE.match(trash_rel):
        raise ValueError("非法的回收站路径。")
    workspace = Path(workspace)
    src = workspace / trash_rel
    if not src.is_file():
        raise LookupError(f"回收站记录不存在：{trash_rel}")
    parsed = _parse_entry(src.name)
    if parsed is None:
        raise ValueError("回收站文件名无法解析，禁止恢复。")
    orig, _ = parsed
    ref = parse_relpath(orig)
    if ref is None:
        raise ValueError(f"无法识别原产物路径：{orig}")
    dest = workspace / orig
    try:
        dest.resolve().relative_to(workspace.resolve())
    except ValueError:
        raise ValueError(f"恢复目标越界，禁止恢复：{orig}") from None
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 无覆盖落位（DEC-005 不覆盖承诺）：os.link 原子占位，dest 已存在时
    # 抛 FileExistsError；不支持硬链接的文件系统退回 move 前复查（竞态
    # 窗口收窄为复查与 move 之间）。
    try:
        os.link(src, dest)
    except FileExistsError:
        raise ValueError(f"原位已有新文件（{orig}）。请先删除重做该新文件，再恢复旧稿。") from None
    except OSError:
        if dest.exists():
            raise ValueError(f"原位已有新文件（{orig}）。请先删除重做该新文件，再恢复旧稿。") from None
        shutil.move(str(src), str(dest))
    else:
        src.unlink()
    logger.info("回收站恢复：%s -> %s", trash_rel, orig)
    return {"status": "ok", "restored": orig}
