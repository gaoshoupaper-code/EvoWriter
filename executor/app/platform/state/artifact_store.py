"""写作产物存储（PR-09 从 ThreadStore 拆出）。

职责单一化：ThreadStore 只管元数据 CRUD，产物文件读写（storyline/
character/worldview）归本类。v8 产物（outline/detail/novel）链路已退役
（REQ-20260930-163019 FR-004/005）——旧 workspace 的历史文件保留磁盘、不读取。

注入 threads repository（touch_thread/write_character 需要 touch 时间戳）。
路径解析仍用 owner 限定的 workspace 目录（与 ThreadStore 共享 workspace_root）。

PR-11 writer 降级时随迁 domains/writing/。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.platform.core.db import ThreadRepository, workspace_dir
from app.schemas.character import CharacterGenerateResponse
from app.schemas.screenplay import (
    CharacterMarkdownFile,
    ObjectMarkdownFile,
    StorylineEntry,
    ThreadSummary,
    WorkspaceCharacterContent,
    WorkspaceObjectContent,
    WorkspaceStorylineContent,
    WorkspaceWorldviewContent,
)


def _read_text(path: Path) -> str:
    """读取文件文本，UTF-8 失败时回退 GB18030（GBK 超集，兼容中文 Windows）。"""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="gb18030", errors="replace")


# 线区块头（与 contracts._BLOCK_HEADER_RE 同构）：## {线名} · {类型} · {状态}
_LINE_BLOCK_HEADER = re.compile(
    r"(?m)^##\s+([^#·•\n]+?)\s*[·•]\s*\**(主线|支线|角色线|暗线)\**(?:\s*[·•]|\s*$)"
)


def _has_line_blocks(markdown: str) -> bool:
    """storyline.md 是否为新格式（含至少一个线区块头）。"""
    return bool(markdown) and _LINE_BLOCK_HEADER.search(markdown) is not None


def _split_line_blocks(markdown: str) -> list[tuple[str, str]]:
    """把 storyline.md 按线区块拆分为 [(线名, 区块文本)]。

    第一个区块头之前的内容（故事核心）不计入返回；区块文本从区块头行起
    到下一个区块头（或文件尾）为止。
    """
    matches = list(_LINE_BLOCK_HEADER.finditer(markdown))
    result: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        result.append((m.group(1).strip().replace("**", ""), markdown[m.start():end].strip()))
    return result


class WritingArtifactStore:
    """写作产物文件读写（owner 限定的 workspace 目录）。

    由 ThreadStore 持有（thread_store.artifacts），或独立注入到 main.py。
    """

    def __init__(
        self,
        workspace_root: Path,
        threads: ThreadRepository,
    ) -> None:
        self.workspace_root = workspace_root
        self.threads = threads

    # ── 路径解析 ─────────────────────────────────────────────
    def _ws_path(self, owner_id: str, workspace_id: str) -> Path:
        return workspace_dir(self.workspace_root, owner_id, workspace_id)

    def _require_ws_path(self, owner_id: str, workspace_id: str) -> Path:
        """owner 限定路径，目录缺失抛 FileNotFoundError，workspace 不存在抛 KeyError。"""
        ws_path = self._ws_path(owner_id, workspace_id)
        if not ws_path.exists():
            raise FileNotFoundError(f"Workspace directory missing: {ws_path}")
        return ws_path

    # ── 产物读取 ─────────────────────────────────────────────
    def read_workspace_storyline(self, owner_id: str, workspace_id: str) -> WorkspaceStorylineContent | None:
        """读故事线产物（REQ-20260930-002231 FR-013/014：双格式）。

        v2（新格式）：storyline.md 含线区块头 → markdown 承载全文，entries 按区块拆分。
        legacy（旧格式）：storyline/ 目录存在且单文件不含区块 → 维持旧读取行为。
        两者并存时以单文件区块判定为准（新格式优先）。
        """
        try:
            ws_path = self._require_ws_path(owner_id, workspace_id)
        except (KeyError, FileNotFoundError):
            return None
        index_path = ws_path / "storyline.md"
        index_markdown = _read_text(index_path) if index_path.exists() else ""
        storyline_dir = ws_path / "storyline"

        if _has_line_blocks(index_markdown) or not storyline_dir.exists():
            # v2：单文件即全部产物；entries 按线区块拆分（title=线名）。
            # storyline/ 目录不存在时（新任务尚无产物 / 新格式）也归 v2。
            entries = [
                StorylineEntry(filename="storyline.md", title=title, markdown=block)
                for title, block in _split_line_blocks(index_markdown)
            ]
            return WorkspaceStorylineContent(
                workspace_id=workspace_id, format="v2", markdown=index_markdown,
                index_markdown=index_markdown, entries=entries, file_count=len(entries),
            )

        # legacy：旧多文件格式（storyline/ 目录）——维持旧行为（FR-013 降级展示）
        entries = []
        if storyline_dir.exists():
            for ap in sorted(storyline_dir.glob("*.md"), key=lambda p: p.name):
                content = _read_text(ap).strip()
                if content:
                    entries.append(StorylineEntry(filename=ap.name, title=ap.stem, markdown=content))
        return WorkspaceStorylineContent(
            workspace_id=workspace_id, format="legacy", markdown=index_markdown,
            index_markdown=index_markdown, entries=entries, file_count=len(entries),
        )

    def read_workspace_worldview(self, owner_id: str, workspace_id: str) -> WorkspaceWorldviewContent | None:
        try:
            ws_path = self._require_ws_path(owner_id, workspace_id)
        except (KeyError, FileNotFoundError):
            return None
        wp = ws_path / "worldview.md"
        return WorkspaceWorldviewContent(
            workspace_id=workspace_id,
            markdown=_read_text(wp) if wp.exists() else "",
        )

    def read_workspace_characters(self, owner_id: str, workspace_id: str) -> WorkspaceCharacterContent | None:
        try:
            ws_path = self._require_ws_path(owner_id, workspace_id)
        except (KeyError, FileNotFoundError):
            return None
        character_dir = ws_path / "character"
        characters: list[CharacterMarkdownFile] = []
        if character_dir.exists():
            for ap in sorted(character_dir.glob("*.md"), key=lambda p: p.stem):
                characters.append(CharacterMarkdownFile(filename=ap.name, name=ap.stem, markdown=_read_text(ap)))
        return WorkspaceCharacterContent(workspace_id=workspace_id, characters=characters)

    def read_workspace_objects(self, owner_id: str, workspace_id: str) -> WorkspaceObjectContent | None:
        """读物品卡产物（REQ-20261004-221109 FR-007）：object/*.md 一物品一文件。

        无 object/ 目录的旧作品返回空列表（空态由前端渲染，不报错）。
        """
        try:
            ws_path = self._require_ws_path(owner_id, workspace_id)
        except (KeyError, FileNotFoundError):
            return None
        object_dir = ws_path / "object"
        objects: list[ObjectMarkdownFile] = []
        if object_dir.exists():
            for ap in sorted(object_dir.glob("*.md"), key=lambda p: p.stem):
                objects.append(ObjectMarkdownFile(filename=ap.name, name=ap.stem, markdown=_read_text(ap)))
        return WorkspaceObjectContent(workspace_id=workspace_id, objects=objects)

    def bootstrap_workspace(
        self, owner_id: str, workspace_id: str, *, ws_exists: bool, threads_rows: list[dict],
        thread_summaries: list[ThreadSummary],
    ) -> dict | None:
        """一次读取全部产物文件，批量返回 bootstrap 数据。

        Args:
            ws_exists: workspace 目录是否存在（由调用方 ThreadStore 判定，避免跨界）。
            threads_rows: workspace 的 thread 原始行（由 ThreadStore 查询）。
            thread_summaries: 已转换的 ThreadSummary 列表（由 ThreadStore 转换）。
        """
        ws_path = self._ws_path(owner_id, workspace_id)
        if not ws_exists and not ws_path.exists():
            raise FileNotFoundError(f"Workspace directory missing: {ws_path}")

        storyline = self.read_workspace_storyline(owner_id, workspace_id)

        worldview_path = ws_path / "worldview.md"
        worldview = WorkspaceWorldviewContent(
            workspace_id=workspace_id,
            markdown=_read_text(worldview_path) if worldview_path.exists() else "",
        )

        character_dir = ws_path / "character"
        characters: list[CharacterMarkdownFile] = []
        if character_dir.exists():
            for ap in sorted(character_dir.glob("*.md"), key=lambda p: p.stem):
                characters.append(CharacterMarkdownFile(filename=ap.name, name=ap.stem, markdown=_read_text(ap)))
        character_content = WorkspaceCharacterContent(workspace_id=workspace_id, characters=characters)

        object_dir = ws_path / "object"
        objects: list[ObjectMarkdownFile] = []
        if object_dir.exists():
            for ap in sorted(object_dir.glob("*.md"), key=lambda p: p.stem):
                objects.append(ObjectMarkdownFile(filename=ap.name, name=ap.stem, markdown=_read_text(ap)))


        return {
            "threads": sorted(thread_summaries, key=lambda t: t.updated_at, reverse=True),
            "storyline": storyline,
            "characters": character_content,
            "worldview": worldview,
            "objects": WorkspaceObjectContent(workspace_id=workspace_id, objects=objects),
        }

    # ── 产物写入 ─────────────────────────────────────────────
    def touch_thread(self, owner_id: str, thread: ThreadSummary) -> None:
        """生成完成后更新 thread 活跃时间（write_outline 退役后的留存语义，FR-005）。

        v8 时代 write_outline 在收尾后回写 outline.md / evaluation.md 并 touch；
        两条产物链路退休后，只有「更新活跃时间」仍是必要副作用。
        """
        self.threads.touch(thread.thread_id, owner_id)

    def write_character(
        self, owner_id: str, thread: ThreadSummary, response: CharacterGenerateResponse,
    ) -> None:
        ws_path = Path(thread.workspace_path)
        if not ws_path.exists():
            raise FileNotFoundError(f"Workspace directory missing: {ws_path}")
        artifact_dir = ws_path / "character"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"{response.name}.md"
        markdown = response.markdown.strip() or self._fallback_character_markdown(response)
        artifact_path.write_text(f"{markdown}\n", encoding="utf-8")
        self.threads.touch(thread.thread_id, owner_id)

    # ── 辅助 ────────────────────────────────────────────────
    def _fallback_character_markdown(self, response: CharacterGenerateResponse) -> str:
        return (
            f"# {response.name}\n\n"
            f"## 角色身份\n\n{response.identity}\n\n"
            f"## 外貌特征\n\n{response.appearance}\n\n"
            f"## 性格与内心\n\n{response.personality}\n\n"
            f"## 关系网络\n\n{response.relationships}\n\n"
            f"## 目前状态\n\n{response.current_state}\n"
        )
