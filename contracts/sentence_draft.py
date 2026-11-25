"""逐句写作的共享规则（REQ-20261125-154544）。

正文 agent 用五个写作工具一句一句写，文件头工具把状态改为「初稿」时整章一次写入。
这里只放不依赖执行端的纯规则：工具名、本轮已写句子的判定、整章文本的拼装，
以及「已写进文件到哪一句」的进程内标记。执行端与正文包共用同一份判定，
所以中断后补存与运行中写入对「哪些句子还没进文件」的答案一致。
"""
from __future__ import annotations

import json
import re
from contextvars import ContextVar
from datetime import date
from pathlib import Path

SENTENCE_TOOLS = ("write_action", "write_dialogue", "write_sensory", "write_reaction", "write_narration")
HEADER_TOOL = "write_chapter_header"
DRAFT_TOOLS = frozenset((*SENTENCE_TOOLS, HEADER_TOOL))
DRAFT_STATUSES = ("起草中", "初稿")
MANIFEST_FILE = "architecture.json"


def rejection(reason: str) -> str:
    """工具拒收时的回执前缀；进化端的成功率统计按「[拒绝]」识别失败。"""
    return f"[拒绝] {reason}"


def package_uses_sentences(package_root) -> bool:
    """正文包是否按句写作：清单里声明了写作工具才算。读不到清单按旧包处理。"""
    if package_root is None:
        return False
    try:
        manifest = json.loads((Path(package_root) / MANIFEST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return any(tool in SENTENCE_TOOLS for agent in manifest.get("agents", []) for tool in agent.get("tools", []))


def validate_sentence(text) -> str | None:
    """只拦两种格式硬伤：空句、带换行。合格返回 None。"""
    if not isinstance(text, str) or not text.strip():
        return "句子是空的，请写一个完整的句子。"
    if "\n" in text or "\r" in text:
        return "一次只写一句，句子中间不能换行。"
    return None


def _field(message, name, default=None):
    return message.get(name, default) if isinstance(message, dict) else getattr(message, name, default)


def _kind(message):
    return _field(message, "type")


def _text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_text(part.get("text") if isinstance(part, dict) else part) for part in value)
    return "" if value is None else str(value)


def _accepted(result) -> bool:
    return _field(result, "status") != "error" and not _text(_field(result, "content")).startswith("[拒绝")


def turn_sentences(messages) -> list[tuple[str, str]]:
    """本轮（最后一条用户消息之后）写作工具已成功写出的句子，按调用顺序：(调用编号, 句子)。

    被拒的调用和还没返回的调用都不算。句子是对话记录里那次调用的参数，不在别处另存。
    """
    start = 0
    for index in range(len(messages) - 1, -1, -1):
        if _kind(messages[index]) == "human":
            start = index + 1
            break
    results = {_field(m, "tool_call_id"): m for m in messages[start:] if _kind(m) == "tool"}
    written = []
    for message in messages[start:]:
        for call in _field(message, "tool_calls") or ():
            result = results.get(call.get("id"))
            if call.get("name") in SENTENCE_TOOLS and result is not None and _accepted(result):
                written.append((call["id"], str((call.get("args") or {}).get("text", "")).strip()))
    return written


def sentences_after(written, flushed_through=None) -> list:
    """已写出的句子里，标记之后的那一段。标记不属于这一批时全部待存。"""
    if not flushed_through:
        return list(written)
    for index, (call_id, _) in enumerate(written):
        if call_id == flushed_through:
            return written[index + 1:]
    return list(written)


def pending_sentences(messages, flushed_through=None) -> list[tuple[str, str]]:
    """本轮已写出、还没写进文件的句子。"""
    return sentences_after(turn_sentences(messages), flushed_through)


def unresolved_draft_calls(messages, own_call=None) -> bool:
    """最后一条模型消息里，除本次调用外是否还有逐句写作调用没返回。"""
    last = messages[-1] if messages else None
    if last is None or _kind(last) != "ai":
        return False
    answered = {_field(m, "tool_call_id") for m in messages if _kind(m) == "tool"}
    return any(call.get("name") in DRAFT_TOOLS and call.get("id") != own_call and call.get("id") not in answered
               for call in _field(last, "tool_calls") or ())


def split_chapter(text: str) -> tuple[list[str], str]:
    """把正文文件拆成（文件头各行, 正文）。文件头 = 开头的标题行及其后连续的引用行。"""
    lines = (text or "").splitlines()
    end = 1 if lines[:1] and lines[0].startswith("# ") else 0
    while end < len(lines) and lines[end].startswith(">"):
        end += 1
    newline = "\r\n" if "\r\n" in text else "\n"
    return lines[:end], newline.join(lines[end:]).strip("\r\n")


def _date_of(status_line: str | None) -> str:
    match = re.search(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", status_line or "")
    return match[0] if match else date.today().isoformat()


def compose_chapter(existing: str, chapter: int, sentences, *, title=None, status: str) -> str:
    """整章文件文本：章名行、状态行、其余原有文件头行，然后是原有正文加攒下的句子。

    原有正文原样保留，新句子接在末尾，一句一段。状态行沿用原日期，没有就用今天。
    """
    existing = existing or ""
    newline = "\r\n" if "\r\n" in existing else "\n"
    header, body = split_chapter(existing)
    old_title = header[0] if header[:1] and header[0].startswith("# ") else None
    old_status = next((line for line in header if line.startswith("> 状态：")), None)
    name = (title or "").strip()
    title_line = f"# 第{chapter:03d}章 · {name}" if name else old_title or f"# 第{chapter:03d}章"
    status_line = f"> 状态：{status}｜{_date_of(old_status)}"
    others = [line for line in header if line not in (old_title, old_status)]
    paragraphs = ([body] if body else []) + [sentence for sentence in sentences if sentence]
    return (newline.join([title_line, status_line, *others]) + newline * 2
            + (newline * 2).join(paragraphs) + newline)


def body_char_count(text: str) -> int:
    """正文字数：不计标题行、引用行和空行。"""
    return sum(len(line.strip()) for line in (text or "").splitlines()
               if line.strip() and not line.startswith(("#", ">")))


class DraftState:
    """一次运行内「已写进文件到哪一句」的标记。persist 在整章写入成功后保存标记。"""

    def __init__(self, flushed_through: str | None = None, persist=None):
        self.flushed_through = flushed_through
        self.persist = persist


DRAFT = ContextVar("sentence_draft_state", default=None)
_IDLE = DraftState()


def draft_state() -> DraftState:
    """当前运行的标记；没有设置时用进程内的空闲标记（工具被单独调用时）。"""
    return DRAFT.get() or _IDLE


def note_flushed(marker: str | None) -> None:
    state = draft_state()
    state.flushed_through = marker
    if state.persist is not None:
        state.persist(marker)
