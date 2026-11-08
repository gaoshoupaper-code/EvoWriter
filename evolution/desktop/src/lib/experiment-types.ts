import type { JudgeGroupInfo } from "./api";
import type { TraceIdentityRun } from "@/lib/trace-display";

export const TASK_TYPES = ["volume_outlines", "chapter_outlines", "chapter_text"] as const;
export type ExperimentTaskType = typeof TASK_TYPES[number];
export const BUILTIN_SUITE = "stage-smoke-v1";
export const RULE_PROFILE = "stage-eval-v2";
export const TASK_LABELS: Record<ExperimentTaskType, string> = {
  volume_outlines: "整本卷纲", chapter_outlines: "单章章纲", chapter_text: "单章正文",
};
export function experimentTaskLabel(kind: ExperimentTaskType, flowVersion?: number) {
  return kind === "chapter_outlines" && flowVersion !== 2 ? "4 卷章纲" : TASK_LABELS[kind];
}
export const PURPOSE_LABELS = { optimization: "优化", validation: "独立终验", regression: "回归" };
export const EXPERIMENT_STATUS: Record<string, string> = {
  pending: "待运行", running: "运行中", recovering: "恢复中", awaiting_review: "待人审",
  exhausted: "轮数耗尽", validation_failed: "终验未通过", failed: "失败", stopping: "停止中",
  stopped: "已停止", stale: "候选已过期", publishing: "发布中", activated: "已生效",
  publish_failed: "发布失败", restored: "已恢复旧版", scoring: "评分中", completed: "已完成",
  evaluation_error: "评测异常", interrupted: "已中断", passed: "已通过", not_improved: "未达改进门槛",
  regression_failed: "回归未通过", exploration: "优化复测", regression: "回归中", evolving: "改版中",
};
export const PHASE_LABELS: Record<string, string> = {
  baseline: "原版评测", evolving: "改进 Harness", exploration: "优化复测", validation: "独立终验",
  regression: "其他能力回归", review: "人工审阅", publish: "发布与加载",
};
export const EXECUTION_PHASE_LABELS: Record<string, string> = {
  pending: "等待执行", theme: "生成主题", outline: "生成总纲", volume: "生成卷纲",
  chapter: "生成章纲", text: "起草正文", review: "审稿", revision: "审后修订",
  completed: "执行完成", recovering: "恢复执行", running: "执行中", stopped: "已停止",
};
export function executionPhaseLabel(phase: string) {
  const [stage, unit] = phase.split(":", 2);
  if (unit && /^\d+$/.test(unit) && Number(unit) > 0) {
    const number = Number(unit);
    if (stage === "chapter") return "生成第" + number + "章章纲";
    if (stage === "text") return "生成第" + number + "章正文";
    if (stage === "volume") return "生成第" + number + "卷卷纲";
  }
  return EXECUTION_PHASE_LABELS[stage] ?? "执行阶段：" + phase;
}
export function candidatePlaceholder(phase: string, status: string, round: number) {
  const active = ["pending", "running", "recovering", "stopping"].includes(status);
  return phase === "evolving" && active ? "第 " + round + " 轮改版中" : "未生成候选";
}
export type ExperimentTarget = {
  task_type: ExperimentTaskType; volume_ids: number[]; chapter?: number | null;
  chapters_per_volume?: number | null;
};
export function experimentTargetLabel(target: ExperimentTarget) {
  if (target.chapter != null) return "第 " + target.chapter + " 章";
  if (target.task_type === "chapter_outlines") return target.volume_ids.length + " 卷全部章纲";
  const scope = "整本 " + target.volume_ids.length + " 卷";
  return target.chapters_per_volume ? scope + " · 每卷 " + target.chapters_per_volume + " 章" : scope;
}
export type ExperimentInput = {
  demand_md: string; files: Record<string, string>; skills: Record<string, unknown>[];
  confirmation_rules: string; input_revision?: string;
};
export type ExperimentPackageInput = {
  title: string; purpose: keyof typeof PURPOSE_LABELS; target: ExperimentTarget; input: ExperimentInput;
};
export type ExperimentPackage = Omit<ExperimentPackageInput, "input"> & {
  id: string; task_type: ExperimentTaskType; input_revision: string; created_at: string;
  suite?: string;
};
export type ExperimentRuleInput = {
  task_type: ExperimentTaskType; name: string;
  profile?: typeof RULE_PROFILE | null;
  dimensions: { key: string; question: string; anchors: Record<string, string>; weight: number }[];
  discipline_rules: string[];
};
export type ExperimentRule = ExperimentRuleInput & {
  id: string; version: number; created_at: string;
};
export type ExperimentThresholds = {
  completion_rate: number; quality_gain: number;
  regression_tolerance: Partial<Record<ExperimentTaskType, number>>;
};
export type ExperimentOptions = {
  task_types: ExperimentTaskType[];
  versions: { version: number; commit: string; note: string }[];
  production_version: number | null;
  models: { id: number; name: string; model: string; is_active: boolean; has_key?: boolean; base_url?: string }[];
  judge_groups: JudgeGroupInfo[];
  defaults?: { rule_versions: Partial<Record<ExperimentTaskType, string>>; thresholds: ExperimentThresholds; max_rounds: number };
  builtin_suite?: { id: string; total: number };
};
export type ExperimentStart = {
  name?: string; task_type: ExperimentTaskType; baseline_version: number; model_config_id: number;
  judge_group_id: number; rule_versions: Record<ExperimentTaskType, string>;
  optimization_ids: string[]; validation_ids: string[];
  regression_ids: Partial<Record<ExperimentTaskType, string[]>>;
  thresholds: ExperimentThresholds;
  max_rounds: number;
};
export type ExperimentMetrics = {
  total: number; completed: number; completion_rate: number; quality: number | null; evaluation_errors: number;
};
export type ExperimentComparison = {
  passed: boolean; baseline: ExperimentMetrics; candidate: ExperimentMetrics; paired_count: number;
  quality_delta: number | null; new_failures: string[];
  changes: { package_id: string; repeat: number; baseline: number | null; candidate: number | null; delta: number | null }[];
  problems: string[];
};
export type ExperimentModel = { id?: number | null; name: string; model: string };
export type ExperimentModels = {
  executor: ExperimentModel | null; judges: ExperimentModel[]; evolver?: ExperimentModel | null;
};
export type ExperimentUsageGroup = {
  calls: number; failed_calls: number;
  input_tokens: number | null; output_tokens: number | null; total_tokens: number | null;
  known: boolean; partial: boolean;
};
export type ExperimentUsageSummary = {
  generation: ExperimentUsageGroup; evaluation: ExperimentUsageGroup; evolution: ExperimentUsageGroup; known: boolean;
};
export type ExperimentEvaluationCall = {
  sample_id: string; judge_id: string | number | null; dimension: string; status: string;
  usage: { input_tokens?: number | null; output_tokens?: number | null; total_tokens?: number | null;
    prompt_tokens?: number | null; completion_tokens?: number | null } | null;
};
export type ExperimentEvaluationUsagePage = { items: ExperimentEvaluationCall[]; total: number; offset: number; limit: number };
export type ExperimentSample = {
  id: string; phase: string; round: number; arm: "baseline" | "candidate"; package_id: string;
  package_title: string; task_type: ExperimentTaskType; repeat: number; attempt: number; status: string;
  task_id: string; trace_ids: string[]; evaluation_trace_ids?: string[]; completed: boolean; checks: Record<string, unknown>;
  scores: null | { overall: number | null; dimensions: Record<string, number | null>; reasons: Record<string, unknown> };
  error: string | null; artifacts: Record<string, string>; usage: Record<string, unknown>;
  input_revision: string; source_commit: string; artifact_digest?: string;
  execution_phase?: string | null; target?: ExperimentTarget; score_retry_at?: number;
};
export type Experiment = {
  id: string; name: string; task_type: ExperimentTaskType; status: string; phase: string; flow_version?: number;
  current_round: number; max_rounds: number; end_reason: string | null; settings: ExperimentStart;
  models?: ExperimentModels;
  progress?: {
    total: number; done: number; completed: number;
    phase?: { name: string; round: number; total: number; done: number; completed: number; active: number };
  };
  baseline: { version: number; commit: string; artifact_digest?: string };
  current_candidate?: null | { commit: string; digest?: string; round: number };
  best_candidate: null | { commit: string; digest: string; round: number; quality: number | null; completion_rate: number };
  rounds: { round: number; status: string; source_commit: string; base_commit: string; summary: string;
    error: string | null; report: Partial<ExperimentComparison>; diff: string; trace_ids?: string[];
    regressions?: Partial<Record<ExperimentTaskType, ExperimentComparison>> }[];
  samples: ExperimentSample[];
  trace_runs?: Record<string, TraceIdentityRun>;
  report: { passed?: boolean; exploration?: ExperimentComparison; validation?: ExperimentComparison;
    regressions?: Partial<Record<ExperimentTaskType, ExperimentComparison>>; reason?: string;
    exploration_round?: number; regression_round?: number; validation_round?: number };
  events: { id: string; kind: string; message: string; created_at: string }[];
  usage: ExperimentUsageSummary; can_read_content: boolean; can_publish: boolean; can_restore?: boolean;
  stale_reason: string | null; publish_status: string; loaded_revision: string | null;
  load_error: string | null; rollback_status: string | null; created_at: string; updated_at: string;
};
export type ExperimentSummary = Omit<Experiment, "rounds" | "samples" | "events" | "usage">;
