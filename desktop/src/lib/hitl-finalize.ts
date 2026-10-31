/**
 * HITL 已答定格：拍板卡被回答（resume 提交）时，把带 awaitingInput 的消息
 * 收敛为可回看的定格形态。
 *
 * 背景（回归修复）：awaitingInput 是提问的唯一载体（行模式下提问不进 content），
 * 原实现 resume 时直接清空该字段——提问文案随拍板卡一起蒸发，且消息的
 * executionPhase 停在 asking，状态卡永远挂着「已暂停 · 等你回答」僵尸摘要。
 *
 * 定格语义：本轮执行以用户拍板收尾（status: "answered"，区别于 completed 交付），
 * resume 后的续跑输出归新一轮 assistant 消息。三件事：
 * 1. 提问沉淀——行模式把 question 追加为正文行（时间线与派生正文均可回看）；
 *    旧管线 interrupt 时 question 已写入 content，无需处理。
 * 2. 终态落章——status: "answered"，恢复/回看时派生稳定（不依赖瞬态 phase）。
 * 3. phase 重算——消除僵尸 asking 态（D4 直改消息不走 updateAssistantMessage，
 *    须在此显式重派生）。
 */
import { derivePhaseFromMessage } from "./execution-phase";
import { appendQuestionRow, concatTextRows } from "./rows";
import type { ChatMessage } from "./types";

export function finalizeAnsweredMessage(message: ChatMessage, now: number = Date.now()): ChatMessage {
  if (!message.awaitingInput) return message;
  const hitl = message.awaitingInput;
  const shouldSettle = !!message.rows?.length && !!hitl.question;
  const rows = shouldSettle ? appendQuestionRow(message.rows!, hitl.question, now) : message.rows;
  const next: ChatMessage = {
    ...message,
    awaitingInput: undefined,
    status: "answered",
    ...(rows !== message.rows ? { rows, content: concatTextRows(rows) } : {}),
  };
  return { ...next, executionPhase: derivePhaseFromMessage(next) };
}
