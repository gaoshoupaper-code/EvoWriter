"""跨卷循环必须等本卷全部章纲和正文完成。"""
from pathlib import Path

import pytest

from app.domains.writing.pipeline import route
from contracts.writing_artifacts import (
    load_writing_state,
    next_stage_on_confirm,
    save_writing_state,
    scan_writing_progress,
)


def _write(workspace: Path, rel: str, text: str = "# 已完成\n") -> None:
    path = workspace / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _volume(workspace: Path, number: int, span: str, *, draft: bool = False) -> None:
    status = "起草中" if draft else "定稿"
    _write(workspace, f"卷纲/第{number:02d}卷.md", (
        f"# 第{number}卷\n> 状态：{status}\n\n"
        f"## 发展阶段\n阶段\n## 爽点节奏表\n{span}\n节奏\n"
        "## 爽点质检表\n质检\n## 卷末钩子\n钩子\n"
    ))


@pytest.fixture
def book(tmp_path: Path) -> Path:
    _write(tmp_path, "总纲.md", (
        "# 总纲\n> 状态：定稿\n## 核心纲\n"
        "| 卷 | 小目标 |\n|---|---|\n"
        "| 一 | 第一卷 |\n| 二 | 第二卷 |\n| 三 | 第三卷 |\n| 四 | 第四卷 |\n"
    ))
    _volume(tmp_path, 1, "本卷章号范围：1-30")
    return tmp_path


def _chapters(workspace: Path, numbers: range) -> None:
    for chapter in numbers:
        _write(workspace, f"章纲/第{chapter:03d}章.md")
        _write(workspace, f"正文/第{chapter:03d}章.md")


def _volume_directive(workspace: Path) -> str:
    state = load_writing_state(workspace)
    state.confirmed_stage = "volume"
    save_writing_state(workspace, state)
    return route(workspace, "conversation").directive


def test_empty_stock_after_first_batch_stays_on_current_volume(book: Path) -> None:
    _chapters(book, range(1, 4))

    assert scan_writing_progress(book).volume_chapter_ranges == {1: (1, 30)}
    assert next_stage_on_confirm(book, "text") == "chapter"
    assert next_stage_on_confirm(book, "chapter") == "chapter"
    assert "当前活卷：第 1 卷" in _volume_directive(book)


def test_completed_volume_targets_second_volume(book: Path) -> None:
    _chapters(book, range(1, 31))

    assert next_stage_on_confirm(book, "chapter") == "volume"
    assert "当前目标：第 2 卷卷纲" in _volume_directive(book)


def test_last_unwritten_chapter_goes_to_text(book: Path) -> None:
    _chapters(book, range(1, 30))
    _write(book, "章纲/第030章.md")

    assert next_stage_on_confirm(book, "chapter") == "text"
    assert "当前活卷：第 1 卷" in _volume_directive(book)


def test_missing_outline_does_not_skip_to_next_volume(book: Path) -> None:
    _chapters(book, range(1, 31))
    (book / "章纲/第015章.md").unlink()

    assert next_stage_on_confirm(book, "chapter") == "chapter"
    assert "当前活卷：第 1 卷" in _volume_directive(book)


@pytest.mark.parametrize("span", [
    "", "本卷章号范围：30-1", "本卷章号范围：0-30", "本卷章号范围：1-3000",
    "本卷章号范围：1-30\n本卷章号范围：1-30",
])
def test_unavailable_chapter_range_does_not_skip_volume(book: Path, span: str) -> None:
    _chapters(book, range(1, 31))
    _volume(book, 1, span)

    assert next_stage_on_confirm(book, "chapter") == "chapter"
    assert "当前活卷：第 1 卷" in _volume_directive(book)


@pytest.mark.parametrize("draft", [False, True])
def test_second_volume_stays_active_after_first_volume_complete(book: Path, draft: bool) -> None:
    _chapters(book, range(1, 31))
    _volume(book, 2, "本卷章号范围：31-60", draft=draft)

    assert "当前活卷：第 2 卷" in _volume_directive(book)


def test_final_volume_does_not_target_nonexistent_fifth_volume(book: Path) -> None:
    _chapters(book, range(1, 121))
    for number in range(2, 5):
        _volume(book, number, f"本卷章号范围：{(number - 1) * 30 + 1}-{number * 30}")

    assert next_stage_on_confirm(book, "chapter") == "chapter"
    assert "当前活卷：第 4 卷" in _volume_directive(book)
