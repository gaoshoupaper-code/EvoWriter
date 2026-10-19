/**
 * 恢复对账（D-对账）：本地陈旧消息 × 服务端 run 真相 → 纠正后的消息列表。
 *
 * 背景：SSE interrupt（提问）帧只经当时那条连接送达，断连后服务端按设计
 * 保持 awaiting_input 等 resume，而客户端恢复的占位消息永远停在「正在执行...」，
 * 两端状态机永久分叉。恢复会话时用服务端 run-state 收敛本地视图。
 *
 * 纯函数（便于测试）：只改尾部 assistant 陈旧消息（无 status 且无卡点），
 * 其余消息原样返回；无纠正必要时返回 null（调用方据此不动 state）。
 */
import type { ThreadRunState } from "./api";
import type { ChatMessage } from "./types";

const PLACEHOLDER = "正在执行...";

/** 占位文案（或空正文）替换为说明；真实内容一律保留。 */
function withNote(content: string | undefined, note: string): string {
  return !content || content === PLACEHOLDER ? note : content;
}

export function reconcileMessagesWithRunState(
  messages: ChatMessage[],
  runState: ThreadRunState,
  now = new Date().toISOString(),
): ChatMessage[] | null {
  const last = messages[messages.length - 1];
  // 只对账尾部 assistant 的陈旧态：已有终态或卡点（恢复管线可能已降级处理过）就不动
  if (!last || last.role !== "assistant" || last.status || last.awaitingInput) return null;
  // 消息记录的 trace 与服务端最新 run 对不上（他端提交过新 run）→ 保守不动
  if (last.traceId && runState.trace_id && last.traceId !== runState.trace_id) return null;

  let patch: Partial<ChatMessage>;
  if (!runState.trace_id || runState.status === "idle") {
    // 服务器无记录：占位永不兑现，落 stopped（StoppedView 自带重试入口）
    patch = {
      status: "stopped",
      content: withNote(last.content, "连接曾中断，本次任务未在服务器留下记录。"),
    };
  } else if (runState.status === "running") {
    // 服务端真在跑：「正在执行...」是诚实的，不动（跑完后下次对账收敛）
    return null;
  } else if (runState.status === "awaiting_input") {
    // 提问重建失败（如图像评审形态）：宁缺毋滥，保持现状不误渲染拍板卡
    if (!runState.awaiting) return null;
    const a = runState.awaiting;
    patch = {
      traceId: runState.trace_id,
      // 占位换成问题文本（对齐非行模式 interrupt 落 content 的既有形态）；
      // 行模式持久化的真实正文原样保留（提问由卡展示，不覆盖正文）
      content: withNote(last.content, a.question || "等待你的输入"),
      awaitingInput: {
        kind: a.kind || "choice",
        question: a.question,
        options: a.options ?? null,
        multi_select: a.multi_select ?? false,
        source: a.source ?? undefined,
        askedAt: a.asked_at ?? now,
      },
    };
  } else if (runState.status === "completed") {
    patch = {
      status: "completed",
      content: withNote(last.content, "已完成（断线期间任务在服务器跑完），产物已写入工作区。"),
    };
  } else if (runState.status === "failed") {
    patch = {
      status: "failed",
      content: withNote(last.content, "⚠️ 任务失败（恢复会话时对账发现）。"),
    };
  } else {
    // cancelled：落 stopped（含 StoppedView 重试入口）
    patch = {
      status: "stopped",
      content: withNote(last.content, "任务已停止（恢复会话时对账发现）。"),
    };
  }

  const next = [...messages];
  next[next.length - 1] = { ...last, ...patch };
  return next;
}
