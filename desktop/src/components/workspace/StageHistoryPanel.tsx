/**
 * StageHistoryPanel —— 阶段历史（REQ-20261018-175526 FR-008/009，DEC-002/004）。
 *
 * 两态（historyView.threadId 区分）：
 * - 列表态：该阶段全部历史会话，时间倒序、自动命名「阶段 · 日期时间」
 *   （跳段往返带「（正文申请）」），超过一屏折叠「更早」；未记录阶段的存量
 *   会话归底部「早期对话」小节（需求风险条目兜底）；单条可删（二次确认）。
 * - 查看态：只读浏览该会话消息，无输入框，底部固定「已归档不能续写」提示。
 *
 * 点阶段只查看、不创建会话（DEC-002）；「修改产物」只能回当前阶段对话
 * 或走下游申请（跳段），历史不提供任何继续入口。
 */
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { markdownTableComponents } from "@/lib/markdown-components";
import type { ChatMessage, ThreadSummary } from "@/lib/types";
import { restoreThreadMessages } from "@/lib/session-persist";
import { useExecutionStore } from "@/stores/execution";
import { useStageSessionStore, stageThreadDisplayName, threadsForStage } from "@/stores/stage-session";
import { useWorkspaceStore } from "@/stores/workspace";
import { stageLabel } from "@/lib/pipeline-steps";
import { ConfirmDialog } from "./ConfirmDialog";

/** 平铺条数上限：超过部分折叠进「更早」（DEC-004） */
const FLAT_LIMIT = 15;

type StageHistoryPanelProps = {
  onDeleteThread: (threadId: string) => void | Promise<void>;
};

function HistoryMessageList({ messages }: { messages: ChatMessage[] }) {
  return (
    <div className="message-list stage-history-messages">
      {messages.map((message, index) => (
        <article className={`message ${message.role}`} key={`${message.role}-${index}`}>
          <span className="message-role">{message.role === "assistant" ? "小衍" : "你"}</span>
          <div className="message-content">
            {message.role === "assistant" && message.contentFormat === "markdown" ? (
              <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={markdownTableComponents}>{message.content}</ReactMarkdown>
            ) : (
              <p className="message-plaintext">{message.content}</p>
            )}
          </div>
        </article>
      ))}
    </div>
  );
}

/** 查看态：只读加载单条历史会话的消息（内存优先，本地记录兜底，缺失占位）。 */
function HistoryThreadView({ threadId, workspaceId }: { threadId: string; workspaceId: string }) {
  const [messages, setMessages] = useState<ChatMessage[] | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    let ignore = false;
    setMessages(null);
    setMissing(false);
    const cached = useExecutionStore.getState().threadMessages.get(threadId);
    if (cached) {
      setMessages(cached);
      return;
    }
    void restoreThreadMessages(workspaceId, threadId).then((restored) => {
      if (ignore) return;
      if (restored) setMessages(restored);
      else setMissing(true);
    });
    return () => { ignore = true; };
  }, [threadId, workspaceId]);

  if (missing) {
    return <div className="stage-history-missing">该对话的本地记录缺失，无法查看内容。</div>;
  }
  if (!messages) {
    return <div className="stage-history-missing">正在载入对话记录…</div>;
  }
  if (messages.length === 0) {
    return <div className="stage-history-missing">该对话没有消息。</div>;
  }
  return <HistoryMessageList messages={messages} />;
}

export function StageHistoryPanel({ onDeleteThread }: StageHistoryPanelProps) {
  const historyView = useStageSessionStore((s) => s.historyView);
  const meta = useStageSessionStore((s) => s.threadStageMeta);
  const openHistoryThread = useStageSessionStore((s) => s.openHistoryThread);
  const closeHistory = useStageSessionStore((s) => s.closeHistory);
  const threads = useWorkspaceStore((s) => s.threads);
  const workspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const deleting = useWorkspaceStore((s) => s.deleting);
  const [expandedEarly, setExpandedEarly] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<ThreadSummary | null>(null);

  const stage = historyView?.stage ?? "";
  const stageName = stageLabel(stage) ?? "早期";
  // 历史只含归档会话：当前活动会话不出现在任何阶段历史（review P2）
  const activeThreadId = useWorkspaceStore((s) => s.activeThreadId);
  const { matched, early } = useMemo(
    () => threadsForStage(threads, stage, meta, activeThreadId || undefined),
    [threads, stage, meta, activeThreadId],
  );
  const flat = matched.slice(0, FLAT_LIMIT);
  const older = matched.slice(FLAT_LIMIT);

  if (!historyView) return null;

  // ── 查看态：只读单会话 ──
  if (historyView.threadId) {
    const thread = threads.find((t) => t.thread_id === historyView.threadId);
    const m = thread ? meta.get(thread.thread_id) : undefined;
    const name = thread
      ? m
        ? stageThreadDisplayName(m)
        : thread.session_name.trim().slice(0, 15) || "未命名会话"
      : "已删除的对话";
    return (
      <div className="stage-history">
        <div className="stage-history-head">
          <button type="button" className="stage-history-back" onClick={() => useStageSessionStore.setState({ historyView: { stage, threadId: null } })}>
            ← 返回{stageName}历史
          </button>
          <span className="stage-history-title">{name}</span>
        </div>
        <HistoryThreadView threadId={historyView.threadId} workspaceId={workspaceId} />
        <div className="stage-history-readonly-bar">该对话已归档，不能继续。要修改产物，请回到当前阶段对话。</div>
      </div>
    );
  }

  // ── 列表态 ──
  return (
    <div className="stage-history">
      <div className="stage-history-head">
        <button type="button" className="stage-history-back" onClick={closeHistory}>
          ← 返回当前对话
        </button>
        <span className="stage-history-title">{stageName}阶段历史</span>
      </div>
      <div className="stage-history-list">
        {flat.length === 0 && early.length === 0 ? (
          <p className="stage-history-empty">该阶段还没有历史对话。</p>
        ) : null}
        {flat.map(({ thread, meta: m }) => (
          <div className="stage-history-item" key={thread.thread_id}>
            <button type="button" className="stage-history-item-main" onClick={() => openHistoryThread(thread.thread_id)}>
              {stageThreadDisplayName(m)}
            </button>
            <button
              type="button"
              className="stage-history-item-delete"
              onClick={() => setPendingDelete(thread)}
              aria-label={`删除 ${stageThreadDisplayName(m)}`}
            >
              删除
            </button>
          </div>
        ))}
        {older.length > 0 ? (
          <div className="stage-history-older">
            <button type="button" className="stage-history-older-toggle" onClick={() => setExpandedEarly((v) => !v)}>
              {expandedEarly ? "收起更早" : `更早（${older.length}）`}
            </button>
            {expandedEarly
              ? older.map(({ thread, meta: m }) => (
                  <div className="stage-history-item" key={thread.thread_id}>
                    <button type="button" className="stage-history-item-main" onClick={() => openHistoryThread(thread.thread_id)}>
                      {stageThreadDisplayName(m)}
                    </button>
                    <button
                      type="button"
                      className="stage-history-item-delete"
                      onClick={() => setPendingDelete(thread)}
                      aria-label={`删除 ${stageThreadDisplayName(m)}`}
                    >
                      删除
                    </button>
                  </div>
                ))
              : null}
          </div>
        ) : null}
        {early.length > 0 ? (
          <div className="stage-history-early">
            <p className="stage-history-early-title">早期对话（未记录阶段）{early.length > FLAT_LIMIT ? ` 共 ${early.length} 条` : ""}</p>
            {(expandedEarly ? early : early.slice(0, FLAT_LIMIT)).map((thread) => (
              <div className="stage-history-item" key={thread.thread_id}>
                <button type="button" className="stage-history-item-main" onClick={() => openHistoryThread(thread.thread_id)}>
                  {thread.session_name.trim().slice(0, 15) || "未命名会话"}
                </button>
                <button
                  type="button"
                  className="stage-history-item-delete"
                  onClick={() => setPendingDelete(thread)}
                  aria-label={`删除 ${thread.session_name}`}
                >
                  删除
                </button>
              </div>
            ))}
            {early.length > FLAT_LIMIT ? (
              <button type="button" className="stage-history-older-toggle" onClick={() => setExpandedEarly((v) => !v)}>
                {expandedEarly ? "收起更早" : `更早（${early.length - FLAT_LIMIT}）`}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
      <ConfirmDialog
        open={!!pendingDelete}
        title={`删除历史对话「${pendingDelete ? (meta.get(pendingDelete.thread_id) ? stageThreadDisplayName(meta.get(pendingDelete.thread_id)!) : pendingDelete.session_name.trim().slice(0, 15) || "未命名会话") : ""}」？`}
        description="删除后不可恢复，该对话的本地聊天记录会一并清除。"
        confirmLabel="删除对话"
        loading={deleting}
        onConfirm={() => {
          if (!pendingDelete) return;
          const id = pendingDelete.thread_id;
          setPendingDelete(null);
          // 删除的是正在查看的会话 → 回列表（FR-009：删除后列表即时收起该项）
          useStageSessionStore.setState((s) =>
            s.historyView?.threadId === id ? { historyView: { stage: s.historyView.stage, threadId: null } } : {},
          );
          void onDeleteThread(id);
        }}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
