"""作品证据工具集（7 个只读，REQ-20261001-131018 / FR-005/006/007 / DEC-006/007/011）。

绑定 Agent 看作品的三路证据入口（trace 运作史 + 产物双路 + 自身历史会话）：
  - list_work_traces        作品会话记录列表（分页 + harness 版本过滤 + 显式刷新）
  - read_work_trace         单条 trace 分层钻取（概要 → 节点骨架 → 事件区间）
  - read_current_artifacts  当前产物（executor 权威文件，永远最新）
  - list_artifact_revisions 产物版本史（进化侧摄入，含产出 trace 与 harness 版本）
  - read_artifact_revision  读某个修订版正文（过期 → 明确标注不可读）
  - list_agent_sessions     自身历史会话清单（DEC-005 可查历史）
  - read_agent_session      读自身某历史会话的消息记录

约束：
  - 绑定关系一律取自 ctx（workspace_id/agent_id），不接受调用方传作品参数——
    Agent 只能看自己绑定作品的数据（防越权看别人作品）。
  - trace 查询排除进化端自观测 trace（run_purpose LIKE 'evolution%'；
    进化自身 trace 的 workspace 固定为 'evolution'，双保险）。
  - 内容走既有治理管线（摄入时 PayloadGate 已剥离思维链/密钥）——与人在
    进化管理端看到的一致（DEC-007 附注）。
  - 工具返回给 LLM 的正文做长度截断（防单条工具结果撑爆上下文），截断处标注。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.core import db
from app.evolve.ctx import get_tool_context

logger = logging.getLogger("evolution.evolve.agent.tools.evidence")

# 单条工具结果的正文截断上限（字符）。进化分析要的是结构与关键证据，
# 全文阅读靠分段钻取（events 区间 / 分页）。
_MAX_TEXT = 8000


class TraceFilter(BaseModel):
    """list_work_traces 的过滤参数（作品绑定来自 ctx，不在此处）。"""
    status: str | None = Field(None, description="按状态过滤：completed/failed/cancelled/interrupted/running/awaiting_input")
    thread_id: str | None = Field(None, description="按作品的写作会话(thread)过滤")
    harness_version: str | None = Field(None, description="按 harness 版本(commit)过滤——对比改动前后行为")
    since: str | None = Field(None, description="ISO 时间，只返回 started_at >= since")
    until: str | None = Field(None, description="ISO 时间，只返回 started_at <= until")
    limit: int = Field(20, description="单页条数（1-100）")
    offset: int = Field(0, description="分页偏移")
    refresh: bool = Field(False, description="先向 executor 增量拉取再查询（怀疑有更新记录时用）")


def _require_binding() -> tuple[Any, str, str] | None:
    """取 ctx + 绑定校验。未绑定（旧会话）返回 None，工具返回错误提示。"""
    ctx = get_tool_context()
    if ctx is None:
        return None
    if not ctx.agent_id or not ctx.workspace_id:
        return None
    return ctx, ctx.agent_id, ctx.workspace_id


def _truncate(text: str, limit: int = _MAX_TEXT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（已截断，原文 {len(text)} 字符）"


def make_evidence_tools() -> list:
    """构建作品证据工具集（7 个只读，不需 backend）。"""

    # ── FR-005 / DEC-006/011：trace 列表 ──────────────────────

    @tool
    def list_work_traces(
        status: str | None = None,
        thread_id: str | None = None,
        harness_version: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 20,
        offset: int = 0,
        refresh: bool = False,
    ) -> str:
        """列出绑定作品的全部会话记录（trace）——进化证据的主入口。

        每条含：trace_id、所属写作会话、状态、起止时间、耗时、事件数、
        harness 版本（哪个版本的 harness 跑的）、失败原因。
        结果末尾带「数据截止时间」——怀疑有更新的记录时 refresh=true 再查。

        Args:
            status: 按状态过滤（completed/failed/cancelled/interrupted/running/awaiting_input）
            thread_id: 按作品的写作会话(thread)过滤
            harness_version: 按 harness 版本过滤——对比改动前后行为
            since/until: ISO 时间范围（started_at）
            limit/offset: 分页
            refresh: 先向 executor 增量拉取再查询

        用法建议：先浏览全量找异常（failed/耗时长/事件多）；
        用 harness_version 对比改动前后；选中后调 read_work_trace 钻取。
        """
        bound = _require_binding()
        if bound is None:
            return "错误：本会话未绑定作品（旧会话），无作品证据可查"
        _, _, workspace_id = bound
        f = TraceFilter(
            status=status, thread_id=thread_id, harness_version=harness_version,
            since=since, until=until, limit=limit, offset=offset, refresh=refresh,
        )

        refresh_note = ""
        if f.refresh:
            # DEC-006 显式刷新：触发向 executor 的增量拉取（同步兜底扫描）。
            # 失败降级——返回现有数据并注明（FR-005 失败语义）。
            try:
                from app.ingestion.scan import _scan_once
                pulled = _scan_once()
                refresh_note = f"\n（已触发增量同步，本次补摄入 {pulled} 条）"
            except Exception as e:
                logger.warning("list_work_traces 刷新失败（降级返回现有数据）: %s", e)
                refresh_note = f"\n（刷新失败：{e}，以上为进化侧现有数据）"

        where = ["r.workspace_id = ?", "r.run_purpose NOT LIKE 'evolution%'"]
        params: list[Any] = [workspace_id]
        if f.status:
            where.append("r.status = ?")
            params.append(f.status)
        if f.thread_id:
            where.append("r.thread_id = ?")
            params.append(f.thread_id)
        if f.since:
            where.append("r.started_at >= ?")
            params.append(f.since)
        if f.until:
            where.append("r.started_at <= ?")
            params.append(f.until)
        hv_expr = "json_extract(r.run_snapshot_json, '$.harness_version')"
        if f.harness_version:
            where.append(f"{hv_expr} = ?")
            params.append(f.harness_version)
        where_sql = " AND ".join(where)
        limit = max(1, min(f.limit, 100))

        total_row = db.query_one(
            f"SELECT COUNT(*) AS c FROM runs r WHERE {where_sql}", tuple(params))
        total = total_row["c"] if total_row else 0
        rows = db.query_all(
            f"""SELECT r.trace_id, r.thread_id, r.session_name, r.status,
                       r.started_at, r.ended_at, r.duration_ms, r.event_count,
                       r.error, r.run_purpose, {hv_expr} AS harness_version
                FROM runs r
                WHERE {where_sql}
                ORDER BY r.started_at DESC LIMIT ? OFFSET ?""",
            tuple(params + [limit, f.offset]),
        )
        cutoff_row = db.query_one(
            "SELECT MAX(ingested_at) AS cutoff FROM runs WHERE workspace_id = ?",
            (workspace_id,),
        )
        cutoff = (cutoff_row or {}).get("cutoff") or "（无摄入记录）"

        if not rows:
            return (
                f"绑定作品暂无已摄入的会话记录（共 {total} 条命中）。"
                f"\n数据截止：{cutoff}{refresh_note}"
            )
        lines = [f"绑定作品会话记录（命中 {total} 条，第 {f.offset + 1}-{f.offset + len(rows)} 条）："]
        for r in rows:
            dur = f"{r['duration_ms'] / 1000:.0f}s" if r.get("duration_ms") else "?"
            hv = r.get("harness_version") or "unknown"
            hv_short = str(hv)[:8]
            err = f" 错误:{r['error'][:60]}" if r.get("error") else ""
            lines.append(
                f"  {r['trace_id']} [{r['status']}] {r['session_name'] or r['thread_id'] or '?'} "
                f"{r['started_at']} {dur} {r['event_count'] or 0}事件 harness@{hv_short}{err}"
            )
        lines.append(f"数据截止：{cutoff}{refresh_note}")
        lines.append("钻取明细调 read_work_trace(trace_id)。")
        return "\n".join(lines)

    # ── FR-005：trace 分层钻取 ─────────────────────────────────

    @tool
    def read_work_trace(
        trace_id: str,
        detail: str = "skeleton",
        from_seq: int | None = None,
        to_seq: int | None = None,
        max_events: int = 50,
    ) -> str:
        """钻取绑定作品的一条 trace（分层取，不整条塞上下文）。

        detail 三层：
        - "summary"：运行概要 + 节点类型统计（最省）
        - "skeleton"：节点树骨架——每个节点的类型/标签/状态/耗时/工具名（推荐起点）
        - "events"：原始事件区间——用 from_seq/to_seq 限定序号范围（默认最近
          max_events 条），看具体交互内容

        建议：summary → skeleton 定位可疑节点 → events 看该区间细节。
        """
        bound = _require_binding()
        if bound is None:
            return "错误：本会话未绑定作品（旧会话），无作品证据可查"
        _, _, workspace_id = bound

        run = db.query_one(
            """SELECT trace_id, workspace_id, thread_id, session_name, status,
                      started_at, ended_at, duration_ms, event_count, error,
                      json_extract(run_snapshot_json, '$.harness_version') AS harness_version
               FROM runs WHERE trace_id = ?""",
            (trace_id,),
        )
        if run is None or run.get("workspace_id") != workspace_id:
            return f"trace {trace_id} 不存在或不属于绑定作品"

        header = (
            f"trace {trace_id}：{run['session_name'] or run['thread_id'] or '?'} "
            f"[{run['status']}] {run['started_at']} ~ {run.get('ended_at') or '…'} "
            f"harness@{str(run.get('harness_version') or 'unknown')[:8]}"
            + (f" 错误:{run['error'][:100]}" if run.get("error") else "")
        )

        if detail == "summary":
            kinds = db.query_all(
                """SELECT kind, COUNT(*) AS c FROM nodes WHERE trace_id = ?
                   GROUP BY kind ORDER BY c DESC""",
                (trace_id,),
            )
            stats = "、".join(f"{k['kind']} {k['c']}" for k in kinds) or "无节点投影"
            return f"{header}\n节点统计：{stats}\n（调 detail='skeleton' 看结构）"

        if detail == "skeleton":
            nodes = db.query_all(
                """SELECT node_id, parent_node_id, kind, label, status,
                          duration_ms, tool_name, model_name, error
                   FROM nodes WHERE trace_id = ?
                   ORDER BY started_at LIMIT 200""",
                (trace_id,),
            )
            if not nodes:
                return f"{header}\n（无节点投影——调 detail='events' 看原始事件）"
            lines = [header, "节点骨架（≤200）："]
            for n in nodes:
                dur = f"{n['duration_ms'] / 1000:.0f}s" if n.get("duration_ms") else ""
                err = f" ✗{n['error'][:40]}" if n.get("error") else ""
                lines.append(
                    f"  [{n['kind']}] {n['label'] or n['tool_name'] or n['node_id'][:12]} "
                    f"{n['status'] or ''} {dur}{err}"
                )
            return "\n".join(lines)

        # detail == "events"
        seq_where = "trace_id = ?"
        seq_params: list[Any] = [trace_id]
        if from_seq is not None:
            seq_where += " AND sequence >= ?"
            seq_params.append(from_seq)
        if to_seq is not None:
            seq_where += " AND sequence <= ?"
            seq_params.append(to_seq)
        max_events = max(1, min(max_events, 200))
        rows = db.query_all(
            f"""SELECT sequence, type, timestamp, payload_json FROM event_payloads
                WHERE {seq_where}
                ORDER BY sequence DESC LIMIT ?""",
            tuple(seq_params + [max_events + 1]),
        )
        has_more = len(rows) > max_events
        rows = list(reversed(rows[:max_events]))
        if not rows:
            return f"{header}\n（该区间无事件）"
        lines = [header, f"事件（#{rows[0]['sequence']} ~ #{rows[-1]['sequence']}，新→旧已按序排列）：" + ("（还有更早事件未取）" if has_more else "")]
        import json as _json
        for r in rows:
            try:
                payload = _json.loads(r["payload_json"])
            except Exception:
                payload = {}
            evt_type = payload.get("type") or r["type"]
            brief = _event_brief(payload)
            lines.append(f"  #{r['sequence']} {evt_type} {brief}")
        return _truncate("\n".join(lines))

    # ── FR-006 / DEC-007：当前产物 ────────────────────────────

    @tool
    def read_current_artifacts() -> str:
        """读绑定作品的当前产物（executor 权威文件，永远最新）。

        返回故事线(storyline)、世界观(worldview)、角色档案(characters)的
        当前全文（超长截断）。查「产物怎么演变来的」调 list_artifact_revisions。
        """
        bound = _require_binding()
        if bound is None:
            return "错误：本会话未绑定作品（旧会话），无作品证据可查"
        _, _, workspace_id = bound

        from app.evolve.executor_client import (
            ExecutorUnavailableError,
            WorkNotFoundError,
            fetch_workspace_artifacts,
        )
        try:
            data = fetch_workspace_artifacts(workspace_id)
        except WorkNotFoundError:
            return "作品已删除：executor 报告该作品不存在（DEC-010）。历史 trace 与版本史仍可用。"
        except ExecutorUnavailableError as e:
            return f"错误：当前产物读取失败（executor 不可达）：{e}"

        parts: list[str] = [f"作品「{data.get('title', '?')}」当前产物（executor 权威文件）："]
        storyline = data.get("storyline") or {}
        md = storyline.get("markdown") or ""
        if md.strip():
            entries = storyline.get("entries") or []
            parts.append(f"〔故事线〕{len(entries)} 条线：\n{_truncate(md)}")
        else:
            parts.append("〔故事线〕（尚未产出）")
        worldview = (data.get("worldview") or {}).get("markdown") or ""
        parts.append(f"〔世界观〕\n{_truncate(worldview) if worldview.strip() else '（尚未产出）'}")
        characters = (data.get("characters") or {}).get("characters") or []
        if characters:
            char_lines = [f"共 {len(characters)} 个角色档案："]
            for c in characters:
                char_lines.append(f"── {c.get('name', '?')}（{c.get('filename', '')}）")
                char_lines.append(_truncate(c.get("markdown") or "", 2000))
            parts.append("〔角色〕\n" + "\n".join(char_lines))
        else:
            parts.append("〔角色〕（尚未产出）")
        return "\n\n".join(parts)

    # ── FR-006 / DEC-007：产物版本史 ──────────────────────────

    @tool
    def list_artifact_revisions() -> str:
        """列绑定作品的产物版本史（进化侧摄入的修订记录）。

        按产物（logical_key）分组：每组含修订数、各修订的产出 trace、
        harness 版本、时间；正文已过期的修订标注「已过期」不可读。
        判断「改动有没有变好」时，用版本史对照同一产物的前后版本。
        """
        bound = _require_binding()
        if bound is None:
            return "错误：本会话未绑定作品（旧会话），无作品证据可查"
        _, _, workspace_id = bound

        rows = db.query_all(
            """SELECT r.artifact_revision_id, r.content_hash, r.producer_trace_id,
                      r.harness_version, r.created_at,
                      a.artifact_type, a.logical_key,
                      p.expires_at, p.deleted_at
               FROM artifacts a
               JOIN artifact_revisions r ON r.artifact_id = a.artifact_id
               LEFT JOIN payload_objects p ON p.payload_id = r.payload_id
               WHERE a.workspace_id = ?
               ORDER BY a.logical_key, r.created_at""",
            (workspace_id,),
        )
        if not rows:
            return "绑定作品暂无产物修订记录（捕获白名单内的写入才会产生修订）。"
        from app.trace_payloads import _expired
        grouped: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            grouped.setdefault(r["logical_key"], []).append(r)
        lines = [f"产物版本史（{len(grouped)} 个产物，共 {len(rows)} 个修订）："]
        for key, revisions in grouped.items():
            lines.append(f"── {key}（{revisions[0]['artifact_type']}，{len(revisions)} 版）")
            for r in revisions:
                expired = bool(r.get("deleted_at")) or _expired(r.get("expires_at"))
                flag = "已过期" if expired else "可读"
                hv = str(r.get("harness_version") or "?")[:8]
                lines.append(
                    f"  {r['artifact_revision_id'][:12]}… {r['created_at']} "
                    f"harness@{hv} 产出:{r['producer_trace_id'] or '?'} [{flag}]"
                )
        lines.append("读正文调 read_artifact_revision(revision_id)。")
        return "\n".join(lines)

    @tool
    def read_artifact_revision(revision_id: str) -> str:
        """读产物某个修订版的正文（版本史对照用）。

        正文已过期的修订返回「已过期」提示（存储成本换的，DEC-007）。
        """
        bound = _require_binding()
        if bound is None:
            return "错误：本会话未绑定作品（旧会话），无作品证据可查"
        _, _, workspace_id = bound

        row = db.query_one(
            """SELECT r.artifact_revision_id, r.payload_id, r.content_hash,
                      r.created_at, a.logical_key, a.workspace_id
               FROM artifact_revisions r
               JOIN artifacts a ON a.artifact_id = r.artifact_id
               WHERE r.artifact_revision_id = ?""",
            (revision_id,),
        )
        if row is None or row.get("workspace_id") != workspace_id:
            return f"修订 {revision_id} 不存在或不属于绑定作品"

        from app.trace_payloads import read_payload
        content = read_payload(row["payload_id"])
        if content is None:
            return (
                f"修订 {revision_id}（{row['logical_key']}）正文已过期，不可读。"
                f"元数据仍在版本史中（DEC-007：正文 90 天过期）。"
            )
        return (
            f"修订 {revision_id}（{row['logical_key']}，{row['created_at']}，"
            f"hash {str(row['content_hash'])[:12]}）：\n{_truncate(str(content))}"
        )

    # ── FR-007 / DEC-005：自身历史会话 ────────────────────────

    @tool
    def list_agent_sessions() -> str:
        """列出你（本进化 Agent）的全部历史会话——「接着上次聊」的入口。

        返回各会话的 id、状态、起止时间。需要看具体聊了什么调
        read_agent_session(session_id)。
        """
        bound = _require_binding()
        if bound is None:
            return "错误：本会话未绑定作品（旧会话）"
        ctx, agent_id, _ = bound

        from app.evolve import db as ev_db
        sessions = ev_db.list_sessions_by_agent(agent_id, limit=100)
        if not sessions:
            return "本 Agent 名下暂无历史会话。"
        lines = [f"本 Agent 历史会话（{len(sessions)} 个，新→旧）："]
        for s in sessions:
            marker = "（当前）" if s["session_id"] == ctx.session_id else ""
            lines.append(
                f"  {s['session_id']} [{s['status']}] {s['created_at']}{marker}"
            )
        return "\n".join(lines)

    @tool
    def read_agent_session(session_id: str, role: str | None = None) -> str:
        """读本 Agent 某历史会话的对话记录（跨会话记忆，DEC-005）。

        只能读自己 Agent 的会话。role 可过滤 user/assistant/tool。
        内容较长会截断——先看 assistant 消息掌握结论，再按需看全量。
        """
        bound = _require_binding()
        if bound is None:
            return "错误：本会话未绑定作品（旧会话）"
        _, agent_id, _ = bound

        session = db.query_one(
            "SELECT session_id, agent_id, status, created_at FROM evolve_sessions "
            "WHERE session_id = ?",
            (session_id,),
        )
        if session is None or session.get("agent_id") != agent_id:
            return f"会话 {session_id} 不存在或不属于本 Agent"

        from app.evolve.evolve_repo import EvolveMessagesRepo
        messages = EvolveMessagesRepo.list_by_session(session_id, limit=500)
        if role:
            messages = [m for m in messages if m.get("role") == role]
        if not messages:
            return f"会话 {session_id} 无消息记录。"
        lines = [f"会话 {session_id}（{session['status']}，{session['created_at']}）消息（{len(messages)} 条）："]
        for m in messages:
            content = (m.get("content") or "").strip()
            if not content:
                continue
            lines.append(f"[{m['role']} #{m['seq']}] {content[:400]}")
        return _truncate("\n".join(lines), _MAX_TEXT * 2)

    return [
        list_work_traces,
        read_work_trace,
        read_current_artifacts,
        list_artifact_revisions,
        read_artifact_revision,
        list_agent_sessions,
        read_agent_session,
    ]


def _event_brief(payload: dict[str, Any]) -> str:
    """事件负载 → 一行摘要（LLM 阅读友好，隐藏长正文）。"""
    evt_type = payload.get("type", "")
    if evt_type in ("llm_request", "llm_response"):
        model = payload.get("model") or payload.get("model_name") or ""
        usage = payload.get("usage") or {}
        tokens = usage.get("total_tokens") or usage.get("output_tokens") or ""
        return f"{model} {tokens}tok".strip()
    if evt_type in ("tool_call", "tool_result"):
        name = payload.get("name") or payload.get("tool") or payload.get("tool_name") or ""
        status = payload.get("status") or ""
        return f"{name} {status}".strip()
    if evt_type == "middleware_intervention":
        return f"{payload.get('middleware', '?')} {payload.get('action') or payload.get('reason', '')}"
    if evt_type == "hitl":
        return f"{payload.get('question', '')[:80]}"
    if evt_type == "skill_activation":
        return f"{payload.get('skill', '?')}"
    msg = payload.get("message") or payload.get("error") or ""
    return str(msg)[:100]


__all__ = ["make_evidence_tools"]
