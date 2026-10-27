import { KeyboardEvent, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { markdownTableComponents } from "../../lib/markdown-components";
import type { ChatMessage } from "../../lib/types";
import type { StageFlow } from "../../lib/stage";
import { InterviewOptions } from "./InterviewOptions";
import { ImageReviewCard } from "./ImageReviewCard";
import { ReviewCard } from "./ReviewCard";
import { ModeToggle } from "./ModeToggle";
import { ExecutionView } from "./ExecutionView";
import { StageBar } from "./StageBar";
import { StageGuide } from "./StageGuide";
import { StageTransitionCard } from "./StageTransitionCard";
import { StageHistoryPanel } from "./StageHistoryPanel";
import { ArrowUp, Square, Maximize2, Minimize2 } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { useContentStore } from "@/stores/content";
import { useStageSessionStore } from "@/stores/stage-session";

type ChatPanelProps = {
  messages: ChatMessage[];
  prompt: string;
  loading: boolean;
  activeThreadId: string;
  hasActiveWorkspace: boolean;
  onPromptChange: (prompt: string) => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
  onResumeSubmit: (resume: string) => Promise<void>;
  onImageReviewSubmit: (resume: import("../../lib/api").ImageReviewResume) => Promise<void>;
  onStop: () => void;
  stageFlows?: (StageFlow | null)[];
  onRetry?: () => void;
  onDeleteThread: (threadId: string) => void | Promise<void>;
};

export function ChatPanel({
  messages,
  prompt,
  loading,
  activeThreadId,
  hasActiveWorkspace,
  onPromptChange,
  onSubmit,
  onResumeSubmit,
  onImageReviewSubmit,
  onStop,
  stageFlows,
  onRetry,
  onDeleteThread,
}: ChatPanelProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [canExpand, setCanExpand] = useState(false);

  const historyView = useStageSessionStore((s) => s.historyView);
  const pendingTransition = useStageSessionStore((s) => s.pendingTransition);
  const openStageHistory = useStageSessionStore((s) => s.openStageHistory);
  const restartThread = useStageSessionStore((s) => s.restartThread);
  const pipeline = useContentStore((s) => s.outlinePipeline);

  // HITL 选项化（DEC-005 单输入面）：最后一条 assistant 处于 awaitingInput 且
  // 带选项时，禁用底部 composer（提交改走拍板卡；卡内补充框是唯一塞料通道）
  const lastMessage = messages[messages.length - 1];
  const awaitingWithOptions =
    lastMessage?.role === "assistant" &&
    (!!lastMessage?.awaitingInput?.options?.length ||
      lastMessage?.awaitingInput?.kind === "review");

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
    // FR-006（REQ-20261019-222403）：生成中不再禁发——同会话消息排队串行，
    // 只有等拍板选项时才拦（选了选项等于回答）。
    if (!awaitingWithOptions) event.currentTarget.form?.requestSubmit();
  }

  // ── 历史浏览态：整个对话区（消息+输入）换成只读历史面板（FR-008）──
  if (historyView) {
    return (
      <section className="conversation-panel panel-surface" aria-label="创作对话">
        <div className="panel-heading conversation-heading">
          <StageBar
            onOpenStageHistory={(stage) => openStageHistory(stage)}
            onRestart={() => restartThread()}
          />
        </div>
        <StageHistoryPanel onDeleteThread={onDeleteThread} />
      </section>
    );
  }

  // 引导语显示条件（FR-003）：还没有任何 user 消息（新会话空页面），非生成中
  const showGuide = !loading && !messages.some((m) => m.role === "user");

  return (
    <section
      className="conversation-panel panel-surface"
      aria-label="创作对话"
    >
      {/* 标题带（REQ-20261018-175526 DEC-001/FR-001）：阶段条取代会话操作区——
          用户可见范围内没有新建/切换会话入口，会话由阶段自动管理 */}
      <div className="panel-heading conversation-heading">
        <StageBar
          onOpenStageHistory={(stage) => openStageHistory(stage)}
          onRestart={() => restartThread()}
        />
      </div>

      {/* 五段制（REQ-20261018-115508 DEC-003）：砍表单纯对话开场——
          对话即入口，新书首轮开场话术由发版的题材段规程决定（executor
          不再注入「第 0 步摸底」指令，2026-10-19 注入禁令）。 */}
      <div className="message-list" ref={listRef}>
        {showGuide && hasActiveWorkspace && pipeline?.kind === "five" ? (
          <StageGuide
            phase={pipeline.phase}
            onPickSuggestion={(text) => onPromptChange(text)}
          />
        ) : null}
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
              {/* FR-003（REQ-20261018-235653）：行消息的正文块在时间线内按位置渲染，
                  底部正文区不重复显示；无正文行的行消息（仅工具/思考/失败说明）仍走底部 */}
              {!(message.role === "assistant" && message.rows?.some((r) => r.kind === "text")) ? (
                <div className="message-content">
                  {message.role === "assistant" && message.contentFormat === "markdown" ? (
                    <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={markdownTableComponents}>{message.content}</ReactMarkdown>
                  ) : (
                    <p className="message-plaintext">{message.content}</p>
                  )}
                </div>
              ) : null}
              {/* HITL：按 awaitingInput.kind 路由渲染（DD4）。
                  choice → InterviewOptions（写作访谈）
                  image_review → ImageReviewCard（文生图评审）
                  review → ReviewCard（审查停等点，REQ-20261026-191719）
                  D3 防御：仅最后一条 assistant 渲染，历史 HITL 残留不渲染。*/}
              {message.role === "assistant" &&
                index === messages.length - 1 &&
                message.awaitingInput ? (
                  <>
                    {/* FR-004（REQ-20260918-235653）：行模式下提问文案随 HITL 卡展示
                        （正文不被提问覆盖，旧管线提问仍写在 content 里，不重复渲染） */}
                    {message.rows?.length &&
                    message.awaitingInput.kind !== "image_review" &&
                    message.awaitingInput.question ? (
                      /* 提问是完整 markdown 文档（段落/表格/加粗），与正文同一套渲染管线；
                         message-content 复用其 markdown 排版作用域 */
                      <div
                        className="message-content interview-question"
                        data-testid="interview-question"
                      >
                        <ReactMarkdown
                          remarkPlugins={[remarkGfm, remarkBreaks]}
                          components={markdownTableComponents}
                        >
                          {message.awaitingInput.question}
                        </ReactMarkdown>
                      </div>
                    ) : null}
                    {message.awaitingInput.kind === "image_review" ? (
                      <ImageReviewCard
                        payload={message.awaitingInput as unknown as import("../../lib/api").ImageReviewInterrupt}
                        onSubmit={onImageReviewSubmit}
                        disabled={loading}
                      />
                    ) : message.awaitingInput.kind === "review" ? (
                      <ReviewCard
                        reviewKind={message.awaitingInput.review_kind ?? "stage"}
                        onSubmit={onResumeSubmit}
                        disabled={loading}
                        traceId={message.traceId}
                      />
                    ) : message.awaitingInput.options?.length ? (
                      <InterviewOptions
                        options={message.awaitingInput.options}
                        multiSelect={!!message.awaitingInput.multi_select}
                        onSubmit={onResumeSubmit}
                        disabled={loading}
                      />
                    ) : null}
                  </>
                ) : null}
              {/* 自动模式违纪代答灰条（REQ-20261026-191719 FR-007）：事后可回查 */}
              {message.role === "assistant" && message.autoAnswers?.length ? (
                <div className="auto-answer-list" data-testid="auto-answer-list">
                  {message.autoAnswers.map((aa, i) => (
                    <div key={i} className="auto-answer-row">
                      自动拍板：{aa.question ? `${aa.question.slice(0, 80)}${aa.question.length > 80 ? "…" : ""} — ` : ""}
                      {aa.answer}
                    </div>
                  ))}
                </div>
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
        {/* 完成卡片（FR-004/DEC-003）：阶段已变的缓冲，挂在旧对话末尾 */}
        {pendingTransition ? (
          <StageTransitionCard onOpenStageHistory={(stage) => openStageHistory(stage)} />
        ) : null}
      </div>

      {/* composer：无卡时输入框是唯一输入（DEC-005 单输入面）；
          完成卡片挂起时输入框保持可用——直接发送即隐式切换（DEC-012） */}
      <form className="chat-composer" onSubmit={onSubmit}>
        <div className={`chat-input-wrap${expanded ? " expanded" : ""}`}>
          <ModeToggle />
          <textarea
            ref={inputRef}
            className="chat-input"
            value={prompt}
            rows={1}
            onChange={(event) => onPromptChange(event.target.value)}
            onKeyDown={handleInputKeyDown}
            disabled={awaitingWithOptions}
            placeholder={
              awaitingWithOptions
                ? "请在上方拍板卡选择并提交"
                : loading
                  ? "生成中，可以直接输入并回车排队发送"
                  : undefined
            }
          />
          {canExpand ? (
            <button
              className="chat-input-expand"
              type="button"
              onClick={() => setExpanded((current) => !current)}
              aria-label={expanded ? "缩小输入框" : "扩大输入框"}
              title={expanded ? "缩小输入框" : "扩大输入框"}
            >
              {expanded ? <Minimize2 size={14} aria-hidden="true" /> : <Maximize2 size={14} aria-hidden="true" />}
            </button>
          ) : null}
          <div className="composer-actions">
            {/* 写作技能入口已收敛到作品侧栏技能模块（REQ-20261025-205943 FR-005/DEC-007） */}
            {loading ? (
              <button className="stop-button" type="button" onClick={onStop} aria-label="停止" title="停止">
                <Square size={14} fill="currentColor" aria-hidden="true" />
              </button>
            ) : (
              <button className="send-button" type="submit" disabled={awaitingWithOptions} aria-label="发送" title="发送">
                <ArrowUp size={16} aria-hidden="true" />
              </button>
            )}
          </div>
        </div>
      </form>
    </section>
  );
}
