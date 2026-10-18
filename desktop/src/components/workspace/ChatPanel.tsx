import { KeyboardEvent, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { markdownTableComponents } from "../../lib/markdown-components";
import type { ChatMessage, ThreadSummary } from "../../lib/types";
import type { StageFlow } from "../../lib/stage";
import { InterviewOptions } from "./InterviewOptions";
import { ImageReviewCard } from "./ImageReviewCard";
import { SessionMenu } from "./SessionMenu";
import { ExecutionView } from "./ExecutionView";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

type ChatPanelProps = {
  messages: ChatMessage[];
  prompt: string;
  loading: boolean;
  threads: ThreadSummary[];
  activeThreadId: string;
  hasActiveWorkspace: boolean;
  sessionMenuOpen: boolean;
  creatingThread: boolean;
  deleting: boolean;
  onPromptChange: (prompt: string) => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
  onResumeSubmit: (resume: string) => Promise<void>;
  onImageReviewSubmit: (resume: import("../../lib/api").ImageReviewResume) => Promise<void>;
  onStop: () => void;
  onToggleSessionMenu: () => void;
  onCloseSessionMenu: () => void;
  onCreateThread: () => void;
  onSelectThread: (threadId: string) => void;
  onDeleteThread: (threadId: string) => void;
  stageFlows?: (StageFlow | null)[];
  onRetry?: () => void;
};

export function ChatPanel({
  messages,
  prompt,
  loading,
  threads,
  activeThreadId,
  hasActiveWorkspace,
  sessionMenuOpen,
  creatingThread,
  deleting,
  onPromptChange,
  onSubmit,
  onResumeSubmit,
  onImageReviewSubmit,
  onStop,
  onToggleSessionMenu,
  onCloseSessionMenu,
  onCreateThread,
  onSelectThread,
  onDeleteThread,
  stageFlows,
  onRetry,
}: ChatPanelProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [canExpand, setCanExpand] = useState(false);

  // HITL 选项化（DEC-005 单输入面）：最后一条 assistant 处于 awaitingInput 且
  // 带选项时，禁用底部 composer（提交改走拍板卡；卡内补充框是唯一塞料通道）
  const lastMessage = messages[messages.length - 1];
  const awaitingWithOptions =
    lastMessage?.role === "assistant" && !!lastMessage?.awaitingInput?.options?.length;

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;

    const lineHeight = Number.parseFloat(window.getComputedStyle(input).lineHeight);
    const collapsedHeight = lineHeight * 3;
    const expandedHeight = lineHeight * 30;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, expanded ? expandedHeight : collapsedHeight)}px`;
    setCanExpand(input.scrollHeight > collapsedHeight + 1);
  }, [expanded, prompt]);

  // 挂载/切换会话时把对话区滚到最底部，直接看到最新消息
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    list.scrollTop = list.scrollHeight;
  }, [activeThreadId]);

  // 挂载时会话消息还在异步加载（列表为空），到达后补一次滚底；
  // 只处理 0→N——流式追加不强制跟随，不打断用户回看历史
  const prevMessageCountRef = useRef(messages.length);
  useEffect(() => {
    const wasEmpty = prevMessageCountRef.current === 0;
    prevMessageCountRef.current = messages.length;
    if (!wasEmpty) return;
    const list = listRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [messages.length]);

  function handleInputKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }

  return (
    <section
      className="conversation-panel panel-surface"
      aria-label="创作对话"
    >
      {/* 标题带简化（REQ-20261013-181350 DEC-008）：去「Dialogue/创作对话」大标题、
          压扁高度对齐 ZCode；「新建会话」「会话菜单」保留在这一行 */}
      <div className="panel-heading conversation-heading">
        <div className="session-actions">
          <button
            className="session-create-button"
            type="button"
            onClick={() => {
              onCreateThread();
              onCloseSessionMenu();
            }}
            disabled={creatingThread || !hasActiveWorkspace}
          >
            {creatingThread ? "创建中" : "新建会话"}
          </button>
          <SessionMenu
            threads={threads}
            activeThreadId={activeThreadId}
            disabled={!hasActiveWorkspace}
            open={sessionMenuOpen}
            deleting={deleting}
            loading={loading}
            onToggle={onToggleSessionMenu}
            onClose={onCloseSessionMenu}
            onSelectThread={onSelectThread}
            onDeleteThread={onDeleteThread}
          />
        </div>
      </div>

      {/* 五段制（REQ-20261018-115508 DEC-003）：砍表单纯对话开场——
          对话即入口，新书首轮由 agent 走题材第 0 步摸底。 */}
      <div className="message-list" ref={listRef}>
        {messages.map((message, index) => {
          // 三步管线阶段徽章（REQ-20261015-161200）：消息带产出段位
          // （世界观/总纲卷纲/章纲），历史双泳道消息保留旧徽章，
          // legacy 与未知值回退「小衍」
          const label =
            message.role === "assistant"
              ? message.agent === "author"
                ? "陪练"
                : message.agent === "worldview"
                  ? "世界观"
                  : message.agent === "outline"
                    ? "总纲卷纲"
                    : message.agent === "chapters"
                      ? "章纲"
                      : message.agent === "storydesign"
                        ? "设计教练"
                        : message.agent === "storystructure"
                          ? "结构教练"
                          : "小衍"
              : "你";
          const isLastAssistant = message.role === "assistant" && index === messages.length - 1;
          const flow = stageFlows?.[index] ?? null;

          return (
            <article
              className={`message ${message.role} animate-in fade-in-0 slide-in-from-bottom-2 duration-300`}
              data-status={message.status}
              key={`${message.role}-${index}`}
            >
              <span className="message-role">{label}</span>
              {/* ExecutionView：取代 StageFlowView，按 executionPhase 渲染小衍执行体验 */}
              {message.role === "assistant" && (isLastAssistant || flow) ? (
                <ExecutionView
                  message={message}
                  loading={loading}
                  isLastAssistant={isLastAssistant}
                  stageFlow={flow}
                  onRetry={onRetry}
                />
              ) : null}
              <div className="message-content">
                {message.role === "assistant" && message.contentFormat === "markdown" ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={markdownTableComponents}>{message.content}</ReactMarkdown>
                ) : (
                  <p className="message-plaintext">{message.content}</p>
                )}
              </div>
              {/* HITL：按 awaitingInput.kind 路由渲染（DD4）。
                  choice → InterviewOptions（写作访谈）
                  image_review → ImageReviewCard（文生图评审）
                  D3 防御：仅最后一条 assistant 渲染，历史 HITL 残留不渲染。*/}
              {message.role === "assistant" &&
              index === messages.length - 1 &&
              message.awaitingInput ? (
                message.awaitingInput.kind === "image_review" ? (
                  <ImageReviewCard
                    payload={message.awaitingInput as unknown as import("../../lib/api").ImageReviewInterrupt}
                    onSubmit={onImageReviewSubmit}
                    disabled={loading}
                  />
                ) : message.awaitingInput.options?.length ? (
                  <InterviewOptions
                    options={message.awaitingInput.options}
                    multiSelect={!!message.awaitingInput.multi_select}
                    onSubmit={onResumeSubmit}
                    disabled={loading}
                  />
                ) : null
              ) : null}
              {/* shimmer 兜底：最后一条 assistant + loading + 无 ExecutionView 内容时
                  （FR-002：思考行到达后 shimmer 退场，时间线本身即执行反馈） */}
              {isLastAssistant && loading && !message.content && !message.tools?.length && !message.thoughts?.length ? (
                <div className="grid gap-2 mt-2">
                  <Skeleton className="h-4 w-3/4" />
                  <Skeleton className="h-4 w-1/2" />
                  <Skeleton className="h-4 w-2/3" />
                </div>
              ) : null}
            </article>
          );
        })}
      </div>

      {/* composer：无卡时输入框是唯一输入（DEC-005 单输入面） */}
      <form className="chat-composer" onSubmit={onSubmit}>
        <div className={`chat-input-wrap${expanded ? " expanded" : ""}`}>
          <textarea
            ref={inputRef}
            className="chat-input"
            value={prompt}
            rows={1}
            onChange={(event) => onPromptChange(event.target.value)}
            onKeyDown={handleInputKeyDown}
            disabled={loading || awaitingWithOptions}
            placeholder={awaitingWithOptions ? "请在上方拍板卡选择并提交" : undefined}
          />
          {canExpand ? (
            <button
              className="chat-input-expand"
              type="button"
              onClick={() => setExpanded((current) => !current)}
              aria-label={expanded ? "缩小输入框" : "扩大输入框"}
              title={expanded ? "缩小输入框" : "扩大输入框"}
            >
              {expanded ? "↙" : "↗"}
            </button>
          ) : null}
        </div>
        <div className="composer-actions">
          {loading ? (
            <Button
              variant="outline"
              className="stop-button min-h-[46px] rounded-[14px] px-4 text-sm font-black text-red-700 border-red-200 hover:bg-red-50"
              type="button"
              onClick={onStop}
            >
              停止
            </Button>
          ) : null}
          <Button
            className="send-button min-h-[46px] rounded-[14px] px-4 text-sm font-black bg-gradient-to-br from-[var(--coral)] to-[var(--gold)] shadow-lg hover:shadow-xl hover:-translate-y-px transition-all"
            type="submit"
            disabled={loading || awaitingWithOptions}
          >
            {loading ? "生成中" : "发送"}
          </Button>
        </div>
      </form>
    </section>
  );
}
