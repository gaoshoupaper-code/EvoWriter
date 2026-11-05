"""Read untrusted trace files without following filesystem aliases."""
from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath
from typing import TextIO


def open_anchored_text(root: Path, relative: str) -> TextIO:
    """Open a canonical relative file beneath a fixed, symlink-free root."""
    path = PurePosixPath(relative)
    if (not relative or "\\" in relative or ":" in relative or path.is_absolute()
            or str(path) != relative or any(part in {".", ".."} for part in path.parts)):
        raise ValueError("Trace 文件路径必须是规范相对路径")
    absolute = Path(root).absolute()
    if ".." in absolute.parts:
        raise ValueError("Trace 根目录不能包含父目录跳转")
    if os.name == "nt":
        target = absolute / Path(*path.parts)
        for node in (target, *target.parents):
            if node.is_symlink() or getattr(node, "is_junction", lambda: False)():
                raise ValueError("Trace 文件不能通过链接访问")
        return target.open("r", encoding="utf-8")

    # Directory descriptors keep the anchor fixed even if a worker renames a
    # directory between validation and reading. O_NOFOLLOW rejects each alias.
    flags = getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory_fd = os.open(absolute.anchor, flags)
    try:
        for part in (*absolute.parts[1:], *path.parts[:-1]):
            next_fd = os.open(part, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory_fd)
    finally:
        os.close(directory_fd)
    try:
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise ValueError("Trace 载荷必须是普通文件")
        return os.fdopen(file_fd, "r", encoding="utf-8")
    except BaseException:
        os.close(file_fd)
        raise
