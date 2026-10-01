"""对话式共创工作台数据访问层（Phase 1，决策 T6/T7）。

两个仓库分别管 evolve_messages 和 evolve_points 表。schema 由 core/db.py 建表，
本层只负责读写 + JSON 序列化。

EvolveMessagesRepo：对话消息 CRUD（user/assistant/system/tool 共表，role 区分）。
  - seq 会话内自增（MAX(seq)+1），UNIQUE(session_id, seq) 保证无冲突
  - assistant 消息的 tool_events / related_points 为 JSON 列

EvolvePointsRepo：进化点 CRUD（propose/update/reject 三态状态机）。
  - status: proposed → accepted / rejected
  - options 为 JSON 数组（结构化备选方案）
  - chosen_option 是 0-based 下标（accepted 时由用户选定）
  - 拍板后从 list_accepted 生成 design_doc.md

为什么是独立文件而非塞进 db.py：db.py 已 1000+ 行（核心 schema + LLM 配置层），
对话/进化点的领域逻辑独立成模块更清晰，也便于后续 Phase 2 的 Agent 工具直接 import。
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core import db


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uuid() -> str:
    return uuid.uuid4().hex


def _dumps_or_none(value: Any) -> str | None:
    """JSON 序列化，None 保持 None（DB 存 NULL）。"""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _loads_or_none(text: str | None) -> Any:
    """JSON 反序列化，None / 空串返回 None。失败返回 None（容错降级）。"""
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


# ════════════════════════════════════════════════════════════
#  EvolveMessagesRepo：对话消息 CRUD
# ════════════════════════════════════════════════════════════


class EvolveMessagesRepo:
    """evolve_messages 表访问层（决策 T6）。

    消息表存一个 session 的全部对话（user/assistant/system/tool 共表）。
    seq 是会话内单调递增序号，前端按 seq 排序渲染。
    """

    @staticmethod
    def append(
        session_id: str,
        *,
        role: str,
        content: str,
        tool_events: list[dict[str, Any]] | None = None,
        related_points: list[str] | None = None,
    ) -> dict[str, Any]:
        """追加一条消息，自动分配 seq 与 id。

        Args:
            role: user / assistant / system / tool
            content: 消息正文（markdown）
            tool_events: 该消息触发的工具调用摘要列表（assistant 专属）
            related_points: 该消息涉及的进化点 id 列表（用于浮窗↔对话联动高亮）
        Returns:
            完整消息 dict（含 id / seq / created_at）。
        """
        msg_id = _uuid()
        now = _now()
        # seq = 当前 session 最大 seq + 1（空表从 1 开始）
        row = db.query_one(
            "SELECT MAX(seq) AS max_seq FROM evolve_messages WHERE session_id = ?",
            (session_id,),
        )
        seq = (row["max_seq"] + 1) if row and row["max_seq"] is not None else 1

        db.execute(
            """INSERT INTO evolve_messages
               (id, session_id, role, content, tool_events, related_points, seq, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg_id, session_id, role, content,
                _dumps_or_none(tool_events),
                _dumps_or_none(related_points),
                seq, now,
            ),
        )
        return {
            "id": msg_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "tool_events": tool_events,
            "related_points": related_points,
            "seq": seq,
            "created_at": now,
        }

    @staticmethod
    def list_by_session(
        session_id: str, *, after_seq: int | None = None, limit: int = 500,
    ) -> list[dict[str, Any]]:
        """按 seq 升序列出 session 的消息。

        Args:
            after_seq: 增量拉取——只返回 seq > after_seq 的消息；None = 全量
            limit: 上限（防意外大查询）
        Returns:
            [{id, session_id, role, content, tool_events, related_points, seq, created_at}, ...]
            tool_events / related_points 已反序列化为原生类型。
        """
        if after_seq is not None:
            rows = db.query_all(
                """SELECT id, session_id, role, content, tool_events, related_points, seq, created_at
                   FROM evolve_messages
                   WHERE session_id = ? AND seq > ?
                   ORDER BY seq ASC LIMIT ?""",
                (session_id, after_seq, limit),
            )
        else:
            rows = db.query_all(
                """SELECT id, session_id, role, content, tool_events, related_points, seq, created_at
                   FROM evolve_messages
                   WHERE session_id = ?
                   ORDER BY seq ASC LIMIT ?""",
                (session_id, limit),
            )
        return [EvolveMessagesRepo._row_to_dict(r) for r in rows]

    @staticmethod
    def get_by_id(message_id: str) -> dict[str, Any] | None:
        """按 id 查单条消息。"""
        row = db.query_one(
            """SELECT id, session_id, role, content, tool_events, related_points, seq, created_at
               FROM evolve_messages WHERE id = ?""",
            (message_id,),
        )
        return EvolveMessagesRepo._row_to_dict(row) if row else None

    @staticmethod
    def update_content(
        message_id: str, *, content: str | None = None,
        tool_events: list[dict[str, Any]] | None = None,
        related_points: list[str] | None = None,
    ) -> bool:
        """部分更新消息字段（用于 assistant 消息流式拼接完成后的回写）。

        Returns:
            True 命中已更新；False id 不存在。
        """
        sets: list[str] = []
        params: list[Any] = []
        if content is not None:
            sets.append("content = ?")
            params.append(content)
        if tool_events is not None:
            sets.append("tool_events = ?")
            params.append(_dumps_or_none(tool_events))
        if related_points is not None:
            sets.append("related_points = ?")
            params.append(_dumps_or_none(related_points))
        if not sets:
            row = db.query_one("SELECT id FROM evolve_messages WHERE id = ?", (message_id,))
            return row is not None
        params.append(message_id)
        cur = db.execute(
            f"UPDATE evolve_messages SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        return cur.rowcount > 0

    @staticmethod
    def count_by_session(session_id: str) -> int:
        """统计 session 的消息数（用于旧会话识别——0 条即为旧版会话）。"""
        row = db.query_one(
            "SELECT COUNT(*) AS c FROM evolve_messages WHERE session_id = ?",
            (session_id,),
        )
        return row["c"] if row else 0

    @staticmethod
    def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
        """行 → dict，反序列化 JSON 列。"""
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "role": row["role"],
            "content": row["content"],
            "tool_events": _loads_or_none(row.get("tool_events")),
            "related_points": _loads_or_none(row.get("related_points")),
            "seq": row["seq"],
            "created_at": row["created_at"],
        }


# ════════════════════════════════════════════════════════════
#  EvolvePointsRepo：进化点 CRUD（双轨制权威状态源，决策 T7/B）
# ════════════════════════════════════════════════════════════


class EvolvePointsRepo:
    """evolve_points 表访问层（决策 T7）。

    进化点是 Agent 在 conversing 阶段通过工具调用维护的结构化对象：
      propose_evolution_point → INSERT（status=proposed）
      update_evolution_point  → UPDATE（status=accepted + chosen_option + user_note）
      reject_evolution_point  → UPDATE（status=rejected）

    前端浮窗只认这张表的 status 字段（决策 B 双轨制）。
    """

    @staticmethod
    def propose(
        session_id: str,
        *,
        target: str,
        problem: str,
        options: list[dict[str, Any]],
        recommendation: str | None = None,
        note: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Agent 提出一个新进化点（status=proposed）。

        Args:
            target: 要改的要素（meta_system.md / RetryMiddleware / ...）
            problem: 为什么要改（含 finding 引用）
            options: 备选方案数组 [{description, pros, cons, expected_impact}, ...]
            recommendation: 推荐哪个 option + 理由（自由文本）
            note: Agent 补充说明
            agent_id: 归属进化 Agent（跨会话累积，REQ-20261001-131018 FR-008；旧链路 NULL）
        Returns:
            完整进化点 dict（含 id / seq / status=proposed / created_at）。
        """
        point_id = _uuid()
        now = _now()

        def _next_seq(agent_id: str | None, session_id: str) -> int:
            # seq 按 Agent 全局递增（FR-002 跨会话引用契约）：同一 Agent 的点跨会话
            # 连续编号，`#N` 引用全局唯一。旧链路（无 agent_id）维持按会话编号。
            if agent_id:
                row = db.query_one(
                    "SELECT MAX(seq) AS max_seq FROM evolve_points WHERE agent_id = ?",
                    (agent_id,),
                )
            else:
                row = db.query_one(
                    "SELECT MAX(seq) AS max_seq FROM evolve_points WHERE session_id = ?",
                    (session_id,),
                )
            return (row["max_seq"] + 1) if row and row["max_seq"] is not None else 1

        # 撞唯一约束重试（FR-002，review finding-3）：idx_ep_agent_seq 保证 Agent 内
        # seq 唯一；同 Agent 双会话并发 propose 的 MAX+1 读后写竞态由 IntegrityError
        # 暴露，重查 MAX 重试即可收敛（索引未建的存量窗口期兜底语义不变）。
        seq = _next_seq(agent_id, session_id)
        for _attempt in range(3):
            try:
                db.execute(
                    """INSERT INTO evolve_points
                       (id, session_id, seq, target, problem, options, recommendation, note,
                        status, agent_id, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)""",
                    (
                        point_id, session_id, seq, target, problem,
                        json.dumps(options, ensure_ascii=False),
                        recommendation, note, agent_id, now,
                    ),
                )
                break
            except sqlite3.IntegrityError:
                if agent_id is None or _attempt == 2:
                    raise
                seq = _next_seq(agent_id, session_id)
        # 进化点一对一归属（需求 20260731 REQ-01.3/DEC-20/AC-33）：
        # 表达"为什么提出该计划"，与是否采纳无关。从 problem 文本解析 finding 引用，
        # 反查该 session 绑定评估卷宗的问题实例。解析不到不阻断 propose（可后续治理补录）。
        return {
            "id": point_id,
            "session_id": session_id,
            "seq": seq,
            "target": target,
            "problem": problem,
            "options": options,
            "recommendation": recommendation,
            "note": note,
            "status": "proposed",
            "chosen_option": None,
            "user_note": None,
            "accepted_at": None,
            "design_ref": None,
            "created_at": now,
            "agent_id": agent_id,
        }

    @staticmethod
    def accept(
        point_id: str, *, chosen_option: int, user_note: str | None = None,
    ) -> dict[str, Any] | None:
        """用户采纳该进化点的某个 option（status=accepted）。

        Args:
            chosen_option: 0-based option 下标
            user_note: 用户附加说明
        Returns:
            更新后的完整进化点 dict；point_id 不存在返回 None。
        """
        now = _now()
        cur = db.execute(
            """UPDATE evolve_points
               SET status = 'accepted', chosen_option = ?, user_note = ?, accepted_at = ?
               WHERE id = ?""",
            (chosen_option, user_note, now, point_id),
        )
        if cur.rowcount == 0:
            return None
        return EvolvePointsRepo.get_by_id(point_id)

    @staticmethod
    def reject(point_id: str, *, user_note: str | None = None) -> dict[str, Any] | None:
        """用户否决该进化点（status=rejected）。

        Returns:
            更新后的完整进化点 dict；point_id 不存在返回 None。
        """
        now = _now()
        cur = db.execute(
            """UPDATE evolve_points
               SET status = 'rejected', user_note = ?, accepted_at = ?
               WHERE id = ?""",
            (user_note, now, point_id),
        )
        if cur.rowcount == 0:
            return None
        return EvolvePointsRepo.get_by_id(point_id)

    @staticmethod
    def update_options(
        point_id: str, *, options: list[dict[str, Any]],
        recommendation: str | None = None, note: str | None = None,
    ) -> dict[str, Any] | None:
        """更新进化点的备选方案（用户要求补充/Agent 重新分析时）。

        status 保持不变（仍为 proposed，等待用户再次表态）。
        Returns:
            更新后的完整进化点 dict；point_id 不存在返回 None。
        """
        sets = ["options = ?"]
        params: list[Any] = [json.dumps(options, ensure_ascii=False)]
        if recommendation is not None:
            sets.append("recommendation = ?")
            params.append(recommendation)
        if note is not None:
            sets.append("note = ?")
            params.append(note)
        params.append(point_id)
        cur = db.execute(
            f"UPDATE evolve_points SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        if cur.rowcount == 0:
            return None
        return EvolvePointsRepo.get_by_id(point_id)

    @staticmethod
    def set_design_ref(point_id: str, design_ref: int) -> bool:
        """拍板后回填：把进化点映射到 design_doc 的 change 序号。

        Returns:
            True 命中已更新；False point_id 不存在。
        """
        cur = db.execute(
            "UPDATE evolve_points SET design_ref = ? WHERE id = ?",
            (design_ref, point_id),
        )
        return cur.rowcount > 0

    @staticmethod
    def get_by_id(point_id: str) -> dict[str, Any] | None:
        """按 id 查单个进化点。"""
        row = db.query_one(
            """SELECT id, session_id, seq, target, problem, options, recommendation, note,
                      status, chosen_option, user_note, accepted_at, design_ref, created_at, agent_id
               FROM evolve_points WHERE id = ?""",
            (point_id,),
        )
        return EvolvePointsRepo._row_to_dict(row) if row else None

    @staticmethod
    def resolve_by_ref(
        point_ref: str,
        *,
        agent_id: str | None = None,
        session_id: str = "",
    ) -> dict[str, Any] | None:
        """按多种写法解析进化点（FR-002 跨会话引用契约）。

        支持三种写法（模型在对话里可能任选其一）：
          - 32 位十六进制原始 id（propose 返回值 / 清单注入值）
          - "#N"：Agent 名下全局序号（无 agent_id 的旧链路按会话序号）
          - 纯数字 N：同 "#N"

        同序号撞号（重排前的存量数据）取创建时间最新的一点，保证确定性。
        解析不到返回 None，由调用方组织提示文案。
        """
        ref = (point_ref or "").strip()
        if not ref:
            return None
        # 写法一：32 位 hex 原始 id
        if re.fullmatch(r"[0-9a-fA-F]{32}", ref):
            return EvolvePointsRepo.get_by_id(ref.lower())
        # 写法二：#N / 纯数字 N 序号
        m = re.fullmatch(r"#?(\d+)", ref)
        if m:
            seq = int(m.group(1))
            if agent_id:
                candidates = EvolvePointsRepo.list_by_agent(agent_id)
            else:
                candidates = EvolvePointsRepo.list_by_session(session_id)
            hits = [p for p in candidates if p.get("seq") == seq]
            if not hits:
                return None
            if len(hits) == 1:
                return hits[0]
            return max(
                hits, key=lambda p: (p.get("created_at") or "", p.get("id") or "")
            )
        return None

    @staticmethod
    def list_by_session(session_id: str) -> list[dict[str, Any]]:
        """按 seq 升序列出 session 的全部进化点（浮窗数据源）。"""
        rows = db.query_all(
            """SELECT id, session_id, seq, target, problem, options, recommendation, note,
                      status, chosen_option, user_note, accepted_at, design_ref, created_at, agent_id
               FROM evolve_points
               WHERE session_id = ?
               ORDER BY seq ASC""",
            (session_id,),
        )
        return [EvolvePointsRepo._row_to_dict(r) for r in rows]

    @staticmethod
    def list_by_status(session_id: str, status: str) -> list[dict[str, Any]]:
        """按 status 过滤列出进化点（如查 accepted 用于拍板）。"""
        rows = db.query_all(
            """SELECT id, session_id, seq, target, problem, options, recommendation, note,
                      status, chosen_option, user_note, accepted_at, design_ref, created_at, agent_id
               FROM evolve_points
               WHERE session_id = ? AND status = ?
               ORDER BY seq ASC""",
            (session_id, status),
        )
        return [EvolvePointsRepo._row_to_dict(r) for r in rows]

    @staticmethod
    def count_accepted(session_id: str) -> int:
        """统计 accepted 进化点数（拍板按钮启用条件：≥1）。"""
        row = db.query_one(
            "SELECT COUNT(*) AS c FROM evolve_points WHERE session_id = ? AND status = 'accepted'",
            (session_id,),
        )
        return row["c"] if row else 0

    # ── Agent 维度（跨会话累积，REQ-20261001-131018 DEC-003/FR-008）──

    @staticmethod
    def list_by_agent(agent_id: str) -> list[dict[str, Any]]:
        """按 seq 升序列出 Agent 名下全部进化点（跨会话累积——会话 2 可见会话 1 提的点）。

        仅命中带 agent_id 的行；旧会话的进化点（agent_id NULL）不属于任何 Agent，
        随旧会话只读归档（DEC-002）。
        """
        rows = db.query_all(
            """SELECT id, session_id, seq, target, problem, options, recommendation, note,
                      status, chosen_option, user_note, accepted_at, design_ref, created_at, agent_id
               FROM evolve_points
               WHERE agent_id = ?
               ORDER BY created_at, seq ASC""",
            (agent_id,),
        )
        return [EvolvePointsRepo._row_to_dict(r) for r in rows]

    @staticmethod
    def list_by_agent_status(agent_id: str, status: str) -> list[dict[str, Any]]:
        """按 status 过滤列出 Agent 名下进化点（finalize 取 accepted 用，FR-010）。"""
        rows = db.query_all(
            """SELECT id, session_id, seq, target, problem, options, recommendation, note,
                      status, chosen_option, user_note, accepted_at, design_ref, created_at, agent_id
               FROM evolve_points
               WHERE agent_id = ? AND status = ?
               ORDER BY created_at, seq ASC""",
            (agent_id, status),
        )
        return [EvolvePointsRepo._row_to_dict(r) for r in rows]

    @staticmethod
    def count_accepted_by_agent(agent_id: str) -> int:
        """统计 Agent 名下 accepted 进化点数（拍板启用条件：≥1，跨会话）。"""
        row = db.query_one(
            "SELECT COUNT(*) AS c FROM evolve_points WHERE agent_id = ? AND status = 'accepted'",
            (agent_id,),
        )
        return row["c"] if row else 0

    @staticmethod
    def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
        """行 → dict，反序列化 options JSON。"""
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "seq": row["seq"],
            "target": row["target"],
            "problem": row["problem"],
            "options": _loads_or_none(row.get("options")) or [],
            "recommendation": row.get("recommendation"),
            "note": row.get("note"),
            "status": row["status"],
            "chosen_option": row.get("chosen_option"),
            "user_note": row.get("user_note"),
            "accepted_at": row.get("accepted_at"),
            "design_ref": row.get("design_ref"),
            "created_at": row["created_at"],
            "agent_id": row.get("agent_id"),
        }


