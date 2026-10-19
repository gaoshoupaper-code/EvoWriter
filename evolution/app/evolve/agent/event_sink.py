"""EvolveEventSink — 进化 Agent 专属事件处理器（trace 重构 20260720_154825；
流式行装配 REQ-20261019-183921 FR-004，DEC-001）。

职责：
  消费 LangGraph astream_events 产出的事件，转换为两类结构化帧 dict：
    1. 轮次级帧（model_output / tool_call / tool_output / tool_error）——
       供 _run_agent_streamed 派生消息持久化（evolve_messages）+ run_meta 通知。
       token 不入 trace（D1 原则不变：trace 只存 span + run_meta）。
    2. 行协议帧（row: open/delta/end）——与客户端写作流同构的行寻址 SSE
       （executor WritingEventSink 同款装配），经 stream_hub 广播给前端做
       思考块/正文逐段生长/工具行时间线。行帧不落任何存储；终态快照由
       round 收尾一次性挂到本轮最后一条 assistant 消息（rows_json）。

行装配状态：
  - _row_seq：全局单调递增行序号（客户端按它排时间线）
  - _response_rows：模型调用 run_id → {"reasoning"/"text": row_id}，
    同一响应的思考/正文行共享 response_id；end 帧带全量 text 兜底
    （流式增量与 on_chat_model_end 全量对齐去重）
  - _tool_row_ids / _tool_row_stack：call_id → 工具行；无 call_id 按名回退
  - rows：行终态快照（rows_json 用），open/delta/end 时同步维护

reasoning（DEC-005 降级设计）：on_chat_model_stream 的 chunk.
additional_kwargs.reasoning_content 携带思考增量（DeepSeek-R/GLM thinking
等推理型模型）；非推理模型不产出该字段——自动降级为只有 text 行。
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("evolution.evolve.agent.event_sink")


# ── 进化点工具名集合（用于识别 proposal 事件）──────────────────────

_PROPOSAL_TOOLS = frozenset({
    "propose_evolution_point",
    "update_evolution_point",
    "reject_evolution_point",
})


class EvolveEventSink:
    """进化 Agent 的事件处理器。

    消费轮次级事件（model_end / tool_start / tool_end / tool_error）与
    流式事件（model_stream 的 content / reasoning），产出轮次帧 + 行协议帧。
    sink 只产帧不做 IO（持久化与广播由 _run_agent_streamed 协调）。
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        # 跟踪 LangGraph superstep 最大值（DD5）：session 结束时由
        # _run_agent_streamed 写 step_stats run_meta（DD8）。
        self.max_superstep = 0
        # ── 行装配状态（REQ-20261019-183921 FR-001/FR-002）──
        self._row_seq = 0
        self._response_rows: dict[str, dict[str, str]] = {}
        self._tool_row_ids: dict[str, str] = {}
        self._tool_row_stack: list[tuple[str, str]] = []
        # 行终态快照（rows_json 持久化用），camelCase 对齐前端 TimelineRow。
        self.rows: list[dict[str, Any]] = []

    async def on_event_dicts(self, event: dict) -> list[dict]:
        """处理一个 astream_events 事件，返回结构化帧 dict 列表。

        Args:
            event: LangGraph astream_events(version="v2") 产出的 event dict。

        Returns:
            [{"type": "model_output"|"tool_call"|"tool_output"|"tool_error"|"row", ...}, ...]
            空列表表示该事件不产出帧。
        """
        frames: list[dict] = []
        kind = event.get("event", "")
        name = event.get("name", "")
        data = event.get("data", {}) or {}

        # 跟踪 LangGraph superstep 最大值（DD5）。
        step = (event.get("metadata") or {}).get("langgraph_step")
        if isinstance(step, int) and step > self.max_superstep:
            self.max_superstep = step

        try:
            if kind == "on_chat_model_end":
                frames.extend(self._handle_model_end(event, data))

            elif kind == "on_chain_start" and name == "tools":
                frames.extend(self._handle_tools_start(data))

            elif kind == "on_tool_end":
                frames.extend(self._handle_tool_end(name, data))

            elif kind == "on_tool_error":
                frames.extend(self._handle_tool_error(name, data))

            elif kind == "on_chat_model_stream":
                frames.extend(self._handle_model_stream(event, data))

        except Exception:
            # sink 内部异常不应中断 agent 流——记日志继续
            logger.exception(
                "EvolveEventSink 处理事件异常: session=%s kind=%s name=%s",
                self.session_id, kind, name,
            )

        return frames

    # ── 轮次级事件处理 ─────────────────────────────────────────

    def _handle_model_end(self, event: dict, data: dict) -> list[dict]:
        """on_chat_model_end：Agent 一轮回复完整文本 + 工具调用意图。

        产出 model_output 帧（消息持久化派生）+ 行终态帧（FR-002）：
        未流式的调用在此开行即全量（必丢路径兜底）。
        """
        output = data.get("output")
        text = _extract_model_text(output)
        tool_calls = _extract_tool_calls(output)

        evt: dict[str, Any] = {"type": "model_output", "text": text}
        if tool_calls:
            evt["tool_calls"] = tool_calls

        frames: list[dict] = [evt]
        # 行终态：正文行 end（带全量 text 兜底）+ 思考行 end
        frames.extend(self._row_model_end(event, text))
        return frames

    def _handle_tools_start(self, data: dict) -> list[dict]:
        """on_chain_start(tools)：工具调用开始。

        产出 tool_call 帧（轮次信号）+ 工具行 open 帧（前端时间线）。
        """
        frames: list[dict] = []
        tool_inputs = data.get("input", [])
        if not isinstance(tool_inputs, list):
            return frames

        for tc in tool_inputs:
            if not isinstance(tc, dict):
                continue
            tool_name = tc.get("name", "unknown")
            call_id = tc.get("id", "")
            args = tc.get("args", {}) or {}

            frames.append({
                "type": "tool_call",
                "tool_name": tool_name,
                "input": _summarize_tool_args(tool_name, args),
                "call_id": call_id,
            })
            # 工具行 open（行 id 用 call_id：终态按 call_id 精确回找）
            rid, frame = self._row_open("tool", row_id=call_id or None, tool=tool_name)
            if call_id:
                self._tool_row_ids[call_id] = rid
            self._tool_row_stack.append((rid, tool_name))
            frames.append(frame)

        return frames

    def _handle_tool_end(self, name: str, data: dict) -> list[dict]:
        """on_tool_end：工具调用完成，产出结果摘要 + 工具行终态。"""
        output = data.get("output")
        output_str = _extract_tool_output_text(output)
        call_id = _extract_tool_call_id(data)

        frames: list[dict] = [{
            "type": "tool_output",
            "tool_name": name,
            "output_summary": output_str[:500],  # 截断防帧过大
            "call_id": call_id,
        }]
        frames.extend(self._row_tool_terminal(name, call_id, "complete"))
        return frames

    def _handle_tool_error(self, name: str, data: dict) -> list[dict]:
        """on_tool_error：工具调用错误。产出 tool_error 帧 + 工具行失败终态。"""
        err = data.get("error") or data.get("output") or ""
        call_id = _extract_tool_call_id(data)
        frames: list[dict] = [{
            "type": "tool_error",
            "tool_name": name,
            "call_id": call_id,
            "error": str(err)[:500],
        }]
        frames.extend(self._row_tool_terminal(name, call_id, "failed"))
        return frames

    def _handle_model_stream(self, event: dict, data: dict) -> list[dict]:
        """on_chat_model_stream：流式增量 → 思考/正文行的开行与追加。

        content → text 行；reasoning_content（推理型模型）→ reasoning 行。
        同一 run_id 的行共享 response_id。
        """
        chunk = data.get("chunk")
        content = ""
        if hasattr(chunk, "content"):
            content = chunk.content
        elif isinstance(chunk, dict):
            content = chunk.get("content", "")
        if not isinstance(content, str):
            content = ""

        reasoning = ""
        if hasattr(chunk, "additional_kwargs"):
            reasoning = str(chunk.additional_kwargs.get("reasoning_content", "") or "")
        elif isinstance(chunk, dict):
            msg = chunk.get("message") or chunk
            if isinstance(msg, dict):
                ak = msg.get("additional_kwargs") or {}
                reasoning = str(ak.get("reasoning_content", "") or "")

        if not content and not reasoning:
            return []

        run_id = str(event.get("run_id") or "")
        rows = self._response_rows.setdefault(run_id, {})
        frames: list[dict] = []
        if content:
            rid = rows.get("text")
            if not rid:
                rid, frame = self._row_open("text", response_id=run_id)
                rows["text"] = rid
                frames.append(frame)
            frames.append({"type": "row", "op": "delta", "id": rid, "text": content})
            self._row_delta(rid, content)
        if reasoning:
            rid = rows.get("reasoning")
            if not rid:
                rid, frame = self._row_open("reasoning", response_id=run_id)
                rows["reasoning"] = rid
                frames.append(frame)
            frames.append({"type": "row", "op": "delta", "id": rid, "text": reasoning})
            self._row_delta(rid, reasoning)
        return frames

    # ── 行装配（REQ-20261019-183921，对齐 executor WritingEventSink）──

    def _row_open(self, kind: str, row_id: str | None = None, **extra: Any) -> tuple[str, dict]:
        """开一行并返回 (行 id, row 帧)。行序号全局单调递增。"""
        self._row_seq += 1
        rid = row_id or f"row-{self._row_seq}"
        payload: dict[str, Any] = {"op": "open", "seq": self._row_seq, "id": rid, "kind": kind}
        payload.update(extra)
        payload["type"] = "row"

        snapshot: dict[str, Any] = {
            "seq": self._row_seq,
            "id": rid,
            "kind": kind,
            "status": "streaming",
            "startedAt": int(time.time() * 1000),
        }
        if isinstance(extra.get("response_id"), str):
            snapshot["responseId"] = extra["response_id"]
        if isinstance(extra.get("tool"), str):
            snapshot["toolName"] = extra["tool"]
        self.rows.append(snapshot)
        return rid, payload

    def _row_delta(self, rid: str, text: str) -> None:
        """行增量追加（快照维护；帧由调用方产出）。"""
        for row in self.rows:
            if row["id"] == rid and row["status"] == "streaming":
                row["text"] = (row.get("text") or "") + text
                return

    def _row_model_end(self, event: dict, text: str) -> list[dict]:
        """模型调用结束 → 行终态；未流式的调用在此开行即全量。"""
        run_id = str(event.get("run_id") or "")
        rows = self._response_rows.get(run_id)
        if rows is None:
            if not text:
                return []
            rid, frame = self._row_open("text", response_id=run_id)
            self._response_rows[run_id] = {"text": rid}
            end = self._row_end_frame(rid, "complete", text=text)
            return [frame, end]

        frames: list[dict] = []
        text_rid = rows.get("text")
        if text and not text_rid:
            # 该响应只走了非流式通道：开行 + 全量终态
            text_rid, frame = self._row_open("text", response_id=run_id)
            rows["text"] = text_rid
            frames.append(frame)
        if text_rid:
            # end 携带全量快照：前端整体替换，与流式增量对齐去重。
            # text 提取失败（如 reasoning-only content）时省略——空串会清空
            # 前端已积累的流式增量（对齐 executor 的 if text guard）
            frames.append(self._row_end_frame(text_rid, "complete", text=text or None))
        if rows.get("reasoning"):
            frames.append(self._row_end_frame(rows["reasoning"], "complete"))
        self._response_rows.pop(run_id, None)
        return frames

    def _row_tool_terminal(
        self, name: str, call_id: str | None, state: str,
    ) -> list[dict]:
        """工具行终态；按 call_id 找行，无 call_id 时按工具名从栈顶回退。

        找不到返回空列表（该行停留 streaming，由流结束时前端兜底关行，
        安全降级——对齐 executor 同款语义）。
        """
        rid = self._tool_row_ids.pop(call_id, None) if call_id else None
        if not rid:
            for i in range(len(self._tool_row_stack) - 1, -1, -1):
                if self._tool_row_stack[i][1] == name:
                    rid = self._tool_row_stack.pop(i)[0]
                    break
        else:
            self._tool_row_stack = [item for item in self._tool_row_stack if item[0] != rid]
        if not rid:
            return []
        return [self._row_end_frame(rid, state)]

    def _row_end_frame(
        self, rid: str, state: str, *, text: str | None = None,
    ) -> dict[str, Any]:
        """行终态帧 + 快照收尾（status/endedAt/全量 text 替换）。"""
        payload: dict[str, Any] = {"type": "row", "op": "end", "id": rid, "state": state}
        if text is not None:
            payload["text"] = text
        for row in self.rows:
            if row["id"] == rid and row["status"] == "streaming":
                row["status"] = state
                row["endedAt"] = int(time.time() * 1000)
                if text is not None:
                    row["text"] = text
                break
        return payload


# ── 辅助函数 ─────────────────────────────────────────────────


def _extract_model_text(output: Any) -> str:
    """从 on_chat_model_end 的 output 提取文本内容。

    支持 AIMessage / dict / str 三种格式。
    """
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if hasattr(output, "content"):
        content = output.content
        if isinstance(content, list):
            # 多段 content（reasoning 模型可能返回 [{"type":"text",...}]）
            return "\n".join(
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        return content if isinstance(content, str) else str(content)
    if isinstance(output, dict):
        return output.get("content", "") or ""
    return str(output)


def _extract_tool_calls(output: Any) -> list[dict[str, Any]]:
    """从 on_chat_model_end 的 output 提取工具调用意图。

    返回 [{name, args, id}, ...]。无工具调用返回空列表。
    """
    if output is None:
        return []
    raw_calls = None
    if hasattr(output, "tool_calls"):
        raw_calls = output.tool_calls
    elif isinstance(output, dict):
        raw_calls = output.get("tool_calls")

    if not raw_calls or not isinstance(raw_calls, list):
        return []

    result: list[dict[str, Any]] = []
    for tc in raw_calls:
        if not isinstance(tc, dict):
            continue
        result.append({
            "name": tc.get("name", ""),
            "args": tc.get("args", {}) or tc.get("input", {}),
            "id": tc.get("id", ""),
        })
    return result


def _extract_tool_call_id(data: dict) -> str:
    """从 on_tool_end/on_tool_error 的 data 提取 tool call id。"""
    output = data.get("output")
    if hasattr(output, "tool_call_id"):
        return str(output.tool_call_id) or ""
    if isinstance(output, dict):
        return str(output.get("tool_call_id", "")) or ""
    return ""


def _extract_tool_output_text(output: Any) -> str:
    """从 on_tool_end 的 output 提取工具结果文本。

    支持 ToolMessage（有 .content）/ dict / str 三种格式。
    """
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if hasattr(output, "content"):
        content = output.content
        return content if isinstance(content, str) else str(content)
    if isinstance(output, dict):
        return output.get("content", "") or str(output)
    return str(output)


def _summarize_tool_args(tool_name: str, args: dict) -> dict[str, Any]:
    """工具参数摘要（避免大块 args 拥堵帧）。

    长字段（如 content/code/changes_json）截断到 200 字符 + 标记 truncated。
    """
    if not isinstance(args, dict):
        return {}
    summary: dict[str, Any] = {}
    long_fields = {"content", "code", "changes_json", "applied_json", "options_json", "problem", "rationale", "summary"}
    for k, v in args.items():
        if k in long_fields and isinstance(v, str) and len(v) > 200:
            summary[k] = v[:200] + "...[truncated]"
        else:
            summary[k] = v
    return summary


__all__ = ["EvolveEventSink"]
