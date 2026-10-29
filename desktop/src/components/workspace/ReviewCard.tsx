/**
 * ReviewCard —— 设计内审查停等卡（REQ-20261026-191719 FR-003/004/006/009，TD-003）。
 *
 * request_review 工具挂起时渲染（interrupt kind="review"），三种审查口：
 * - batch（正文批次）：继续写 N 章（N 现场可改，默认沿用上一批）+ 打回（填意见）
 *   + 上一批实际消耗（DEC-014；无数据占位）
 * - style（文风卡）：确认通过 / 打回意见
 * - stage（段产物审）：确认通过 / 打回意见
 *
 * 提交走 resume 通道（与访谈拍板同一 submit 路径，带 trace_id）：
 * 「继续写 N 章」「确认通过」「打回：<意见>」——agent 按提示词语义消化。
 * N 通过 mode 端点一并回写服务端（DEC-007：批次大小持久化，directive 注入）。
 * stage 卡「确认通过」先走段审循环推进（服务端按一卷→章纲→正文→…循环切
 * 指针，resume 后 agent 即按新段规程开工），推进失败不续跑、卡片解锁可重试。
 */
import { useState } from "react";
import { useStageSessionStore } from "@/stores/stage-session";
import { useWorkspaceStore } from "@/stores/workspace";
import { updateWritingMode } from "@/lib/api";

type ReviewCardProps = {
  reviewKind: "batch" | "style" | "stage";
  onSubmit: (resume: string) => Promise<void>;
  disabled?: boolean;
  /** 待审 run 的 trace_id（stage 确认推进段的幂等键） */
  traceId?: string;
  reviewId?: string;
};

const KIND_TITLES: Record<ReviewCardProps["reviewKind"], string> = {
  batch: "正文批次已写完，请审查",
  style: "文风卡已起草，请过目",
  stage: "本段产物已齐，请审查",
};

export function ReviewCard({ reviewKind, onSubmit, disabled, traceId, reviewId }: ReviewCardProps) {
  const batchSize = useStageSessionStore((s) => s.batchSize);
  const lastBatchUsage = useStageSessionStore((s) => s.lastBatchUsage);
  const hydrateModes = useStageSessionStore((s) => s.hydrateModes);
  const confirmStageAdvance = useStageSessionStore((s) => s.confirmStageReviewAdvance);
  const workspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const [nextN, setNextN] = useState(String(batchSize));
  const [rejectOpen, setRejectOpen] = useState(false);
  const [opinion, setOpinion] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const locked = disabled || submitted;

  async function submit(resume: string, batchN?: number, stageAdvance = false) {
    if (locked) return;
    setSubmitted(true);
    // 批次 N 回写服务端（DEC-007）：下一轮 directive 读到新批次大小
    if (batchN && workspaceId) {
      try {
        await updateWritingMode(workspaceId, "text", "auto", batchN);
        void hydrateModes(workspaceId);
      } catch {
        /* 回写失败不阻塞续跑（directive 兜底默认值） */
      }
    }
    // stage 确认先切段（resume 装配读新指针）；失败解锁重试，不带旧段续跑
    if (stageAdvance && workspaceId) {
      const ok = await confirmStageAdvance(workspaceId, traceId ?? "", reviewId);
      if (!ok) {
        setSubmitted(false);
        return;
      }
    }
    try {
      await onSubmit(resume);
    } catch {
      setSubmitted(false); // 失败解锁可重试（与 InterviewOptions 同口径）
    }
  }

  const usageLine = lastBatchUsage
    ? `上一批：${lastBatchUsage.chapters} 章 · 约 ${(lastBatchUsage.totalTokens / 10000).toFixed(1)} 万 tokens`
    : "上一批消耗：暂无数据";

  return (
    <div className="review-card" data-testid="review-card">
      <div className="review-card-title">{KIND_TITLES[reviewKind]}</div>
      {reviewKind === "batch" ? (
        <div className="review-card-row">
          <span className="review-usage">{usageLine}</span>
        </div>
      ) : null}
      {reviewKind === "batch" ? (
        <div className="review-card-actions">
          <label className="review-batch-input">
            继续
            <input
              type="number"
              min={1}
              max={100}
              value={nextN}
              onChange={(e) => setNextN(e.target.value)}
              disabled={locked}
            />
            章
          </label>
          <button
            type="button"
            className="review-primary"
            disabled={locked || !Number.parseInt(nextN, 10)}
            onClick={() => void submit(`继续写 ${Number.parseInt(nextN, 10)} 章`, Number.parseInt(nextN, 10))}
          >
            继续写 {nextN || "?"} 章
          </button>
          <button
            type="button"
            className="review-secondary"
            disabled={locked}
            onClick={() => setRejectOpen((v) => !v)}
          >
            打回修改
          </button>
        </div>
      ) : (
        <div className="review-card-actions">
          <button
            type="button"
            className="review-primary"
            disabled={locked}
            onClick={() => void submit("确认通过", undefined, reviewKind === "stage")}
          >
            确认通过
          </button>
          <button
            type="button"
            className="review-secondary"
            disabled={locked}
            onClick={() => setRejectOpen((v) => !v)}
          >
            打回修改
          </button>
        </div>
      )}
      {rejectOpen ? (
        <div className="review-reject">
          <textarea
            value={opinion}
            onChange={(e) => setOpinion(e.target.value)}
            placeholder="打回意见：只写要改的地方，没提到的内容不会被改动"
            rows={3}
            disabled={locked}
          />
          <button
            type="button"
            className="review-primary danger"
            disabled={locked || !opinion.trim()}
            onClick={() => void submit(`打回：${opinion.trim()}`)}
          >
            提交打回
          </button>
        </div>
      ) : null}
    </div>
  );
}
