import type { JudgeGroupInfo } from "./api";

export const TASK_TYPES = ["volume_outlines", "chapter_outlines", "chapter_text"] as const;
export type ExperimentTaskType = typeof TASK_TYPES[number];
export const TASK_LABELS: Record<ExperimentTaskType, string> = {
  volume_outlines: "整本卷纲", chapter_outlines: "4 卷章纲", chapter_text: "单章正文",
};
export const PURPOSE_LABELS = { optimization: "优化", validation: "独立终验", regression: "回归" };
export const EXPERIMENT_STATUS: Record<string, string> = {
  pending: "待运行", running: "运行中", recovering: "恢复中", awaiting_review: "待人审",
  exhausted: "轮数耗尽", validation_failed: "终验未通过", failed: "失败", stopping: "停止中",
  stopped: "已停止", stale: "候选已过期", publishing: "发布中", activated: "已生效",
  publish_failed: "发布失败", restored: "已恢复旧版", scoring: "评分中", completed: "已完成",
  evaluation_error: "评测异常", interrupted: "已中断",
};
export const PHASE_LABELS: Record<string, string> = {
  baseline: "原版基线", evolving: "改进 Harness", exploration: "探索复测", validation: "独立终验",
  regression: "其他能力回归", review: "人工审阅", publish: "发布与加载",
};

export type ExperimentTarget = { task_type: ExperimentTaskType; volume_ids: number[]; chapter?: number | null };
export type ExperimentInput = {
  demand_md: string; files: Record<string, string>; skills: Record<string, unknown>[];
  confirmation_rules: string; input_revision?: string;
};
export type ExperimentPackageInput = {
  title: string; purpose: keyof typeof PURPOSE_LABELS; target: ExperimentTarget; input: ExperimentInput;
};
export type ExperimentPackage = Omit<ExperimentPackageInput, "input"> & {
  id: string; task_type: ExperimentTaskType; input_revision: string; created_at: string;
};
export type ExperimentRuleInput = {
  task_type: ExperimentTaskType; name: string;
  dimensions: { key: string; question: string; anchors: Record<string, string>; weight: number }[];
  discipline_rules: string[];
};
export type ExperimentRule = ExperimentRuleInput & { id: string; version: number; created_at: string };
export type ExperimentOptions = {
  task_types: ExperimentTaskType[];
  versions: { version: number; commit: string; note: string }[];
  production_version: number | null;
  models: { id: number; name: string; model: string; base_url: string; is_active: boolean }[];
  judge_groups: JudgeGroupInfo[];
};
export type ExperimentStart = {
  name: string; task_type: ExperimentTaskType; baseline_version: number; model_config_id: number;
  judge_group_id: number; rule_versions: Record<ExperimentTaskType, string>;
  optimization_ids: string[]; validation_ids: string[];
  regression_ids: Partial<Record<ExperimentTaskType, string[]>>;
  thresholds: {
    completion_rate: number; quality_gain: number;
    regression_tolerance: Partial<Record<ExperimentTaskType, number>>;
  };
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
export type ExperimentSample = {
  id: string; phase: string; round: number; arm: "baseline" | "candidate"; package_id: string;
  package_title: string; task_type: ExperimentTaskType; repeat: number; attempt: number; status: string;
  task_id: string; trace_ids: string[]; completed: boolean; checks: Record<string, unknown>;
  scores: null | { overall: number | null; dimensions: Record<string, number | null>; reasons: Record<string, unknown> };
  error: string | null; artifacts: Record<string, string>; usage: Record<string, unknown>;
  input_revision: string; source_commit: string; artifact_digest?: string;
};
export type Experiment = {
  id: string; name: string; task_type: ExperimentTaskType; status: string; phase: string;
  current_round: number; max_rounds: number; end_reason: string | null; settings: ExperimentStart;
  progress?: { total: number; done: number; completed: number };
  baseline: { version: number; commit: string; artifact_digest?: string };
  best_candidate: null | { commit: string; digest: string; round: number; quality: number | null; completion_rate: number };
  rounds: { round: number; status: string; source_commit: string; base_commit: string; summary: string;
    error: string | null; report: Partial<ExperimentComparison>; diff: string }[];
  samples: ExperimentSample[];
  report: { passed?: boolean; exploration?: ExperimentComparison; validation?: ExperimentComparison;
    regressions?: Partial<Record<ExperimentTaskType, ExperimentComparison>>; reason?: string };
  events: { id: string; kind: string; message: string; created_at: string }[];
  usage: Record<string, unknown>; can_read_content: boolean; can_publish: boolean;
  stale_reason: string | null; publish_status: string; loaded_revision: string | null;
  load_error: string | null; rollback_status: string | null; created_at: string; updated_at: string;
};
export type ExperimentSummary = Omit<Experiment, "rounds" | "samples" | "events">;
