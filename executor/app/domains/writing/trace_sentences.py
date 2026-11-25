"""从运行记录（Trace）里取回正文已写出的句子（REQ-20261125-154544 DEC-012）。

任务没写完就结束时，执行端按记录把写作工具已写出的句子补进文件。记录由项目自己的
接口读取，不依赖框架检查点的内部格式——新版本框架只存消息增量，直接读检查点取不回完整对话。
"""
from __future__ import annotations

from contracts.sentence_draft import SENTENCE_TOOLS
from app.domains.writing.target_execution import _content, _failed


def recorded_sentences(recorder, workspace, trace_id) -> list[tuple[str, str]]:
    """运行记录里写作工具已成功写出的句子：(调用编号, 句子)，按发生顺序。

    被拒的和出错的调用都不算；没有这份记录时为空。只读，不改动记录器的运行登记。
    """
    if not trace_id or recorder.find_run_by_trace_id(trace_id, workspace_path=str(workspace)) is None:
        return []
    calls: dict[str, dict] = {}
    written: list[tuple[str, str]] = []
    for event in recorder.read_trace_events(trace_id, hydrate_payloads=True) or []:
        args = event.tool_args if isinstance(event.tool_args, dict) else {}
        if event.tool_name not in SENTENCE_TOOLS:
            continue
        if event.type == "tool_start":
            calls[event.tool_call_id] = args
        elif event.type == "tool_end":
            text = calls.pop(event.tool_call_id, args).get("text")
            if (isinstance(text, str) and text.strip()
                    and not _failed(event.tool_output) and not _content(event.tool_output).startswith("[拒绝")):
                written.append((event.tool_call_id, text.strip()))
    return written
