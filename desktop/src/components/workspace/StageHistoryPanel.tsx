/**
 * StageHistoryPanel —— 阶段历史（REQ-20261018-175526 FR-008/009，DEC-002/004）。
 *
 * 两态（historyView.threadId 区分）：
 * - 列表态：该阶段全部历史会话，时间倒序、自动命名「阶段 · 日期时间」
 *   （跳段往返带「（正文申请）」），超过一屏折叠「更早」；未记录阶段的存量
 *   会话归底部「早期对话」小节（需求风险条目兜底）；单条可删（二次确认）。
 * - 查看态：只读浏览该会话消息，无输入框，底部固定「已归档不能续写」提示。
 *   消息来源服务器优先、本地兜底（REQ-20261020-193647 FR-002/DEC-001）：
 *   服务端会话日志是唯一事实源，天然绕开被欢迎语覆盖 bug 污染的本地记录；
 *   两边都无实质记录时诚实占位（DEC-002）。
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
import { restoreFromServerConversation, hasSubstantiveMessages, useExecutionStore } from "@/stores/execution";
import { useStageSessionStore, stageThreadDisplayName, threadsForStage } from "@/stores/stage-session";
import { useWorkspaceStore } from "@/stores/workspace";
import { stageLabel } from "@/lib/pipeline-steps";
import { ConfirmDialog } from "./ConfirmDialog";

/** 平铺条数上限：超过部分折叠进「更早」（DEC-004） */
const FLAT_LIMIT = 15;

/** 服务端腿查看态预算（FR-002 风险条目，REQ-20261020-193647 review #4）：
 *  Rust 中继超时上限 300s（http.rs）对查看态太长——超预算视同不可达、立即
 *  回退本地，不得长停「正在载入」。 */
const SERVER_FETCH_BUDGET_MS = 8000;

/** 流式占位正文（FR-003/REQ-20261020-193647）：带未答提问的消息以提问为准，占位不显示 */
const STREAMING_PLACEHOLDER_CONTENTS = new Set(["正在生成...", "正在优化...", "正在执行..."]);

type StageHistoryPanelProps = {
  onDeleteThread: (threadId: string) => void | Promise<void>;
};

function HistoryMessageList({ messages }: { messages: ChatMessage[] }) {
  return (
    <div className="message-list stage-history-messages">
      {messages.map((message, index) => {
        // FR-003（REQ-20261020-193647）：未答拍板提问在历史可见——行模式提问
        // 不写进正文，正文是流式占位时以提问文本为准；旧管线/图像中断会把
        // 提问写进 content，同文时只显示提问块、不重复（review #9）
        const question = message.role === "assistant" ? message.awaitingInput?.question : undefined;
        const showContent =
          !question ||
          (!STREAMING_PLACEHOLDER_CONTENTS.has(message.content) && message.content !== question);
        return (
          <article className={`message ${message.role}`} key={`${message.role}-${index}`}>
            <span className="message-role">{message.role === "assistant" ? "小衍" : "你"}</span>
            {showContent ? (
              <div className="message-content">
                {message.role === "assistant" && message.contentFormat === "markdown" ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={markdownTableComponents}>{message.content}</ReactMarkdown>
                ) : (
                  <p className="message-plaintext">{message.content}</p>
                )}
              </div>
            ) : null}
            {question ? (
              <div className="message-content interview-question">
                <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={markdownTableComponents}>{question}</ReactMarkdown>
              </div>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}

/** 查看态：服务器优先 → 本地兜底 → 诚实占位（FR-002，DEC-001/002）。 */
function HistoryThreadView({ threadId, workspaceId }: { threadId: string; workspaceId: string }) {
  const [messages, setMessages] = useState<ChatMessage[] | null>(null);
  const [unrecoverable, setUnrecoverable] = useState(false);

  useEffect(() => {
    let ignore = false;
    setMessages(null);
    setUnrecoverable(false);
    void (async () => {
      // 服务器优先（DEC-001）：失败/超时/空统一返回 null，立即转本地兜底；
      // 挂起连接按查看态预算掐断，迟到的结果被下方各层 ignore 守卫丢弃
      const fromServer = await Promise.race([
        restoreFromServerConversation(threadId, { readOnly: true }),
        new Promise<ChatMessage[] | null>((resolve) => {
          setTimeout(() => resolve(null), SERVER_FETCH_BUDGET_MS);
        }),
      ]);
      if (ignore) return;
      if (fromServer) {
        setMessages(fromServer);
        return;
      }
      // 本地兜底：内存缓存 → 本地记录文件。被欢迎语覆盖的污染缓存/记录
      // 无实质内容（FR-002 判据），继续向下一层找，不得直接采信
      const cached = useExecutionStore.getState().threadMessages.get(threadId);
      if (cached && hasSubstantiveMessages(cached)) {
        setMessages(cached);
        return;
      }
      const restored = await restoreThreadMessages(workspaceId, threadId);
      if (ignore) return;
      if (hasSubstantiveMessages(restored)) {
        setMessages(restored);
        return;
      }
      // 服务端与本地都无实质记录（DEC-002）：诚实占位，不再称「本地记录缺失」
      setUnrecoverable(true);
    })();
    return () => { ignore = true; };
  }, [threadId, workspaceId]);

  if (unrecoverable) {
    return <div className="stage-history-missing">该对话的记录无法恢复（早期版本缺陷导致丢失）。</div>;
  }
  if (!messages) {
    return <div className="stage-history-missing">正在载入对话记录…</div>;
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
