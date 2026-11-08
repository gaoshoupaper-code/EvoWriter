export type TraceDisplayContext = {
  workspace_title?: string;
  task_phase?: string;
  task_type?: string;
  target?: { task_type?: string; volume_ids?: number[]; chapter?: number | null; chapters_per_volume?: number | null };
  experiment_name?: string;
  sample_name?: string;
  operation?: "generation" | "evaluation" | "improvement";
  phase?: string;
  arm?: string;
  round?: number;
  repeat?: number;
  legacy?: boolean;
};

export type TraceIdentityRun = {
  trace_id?: string;
  session_name?: string | null;
  started_at?: string | null;
  run_purpose?: string | null;
  purpose?: string | null;
  workload?: string | null;
  status?: string | null;
  display_context?: TraceDisplayContext;
  run_snapshot?: Record<string, unknown>;
};

const TASK_LABELS: Record<string, string> = {
  theme: "题材", outline: "总纲", volume: "卷纲", chapter: "章纲", text: "正文",
  premise: "题材", storybuilding: "总纲", volume_outline: "卷纲", volume_outlines: "卷纲",
  detail_outline: "章纲", chapter_outline: "章纲", chapter_outlines: "章纲",
  writing: "正文", chapter_text: "正文", worldbuilding: "总纲", storystructure: "总纲",
  "character.generate": "人物", "character.repair": "人物补卡", character_repair: "人物修复",
};
const PHASE_LABELS: Record<string, string> = {
  baseline: "原版基线", evolving: "候选改版", improvement: "候选改版",
  exploration: "优化复测", regression: "回归验证", validation: "独立终验",
  review: "人工审核", publish: "发布",
};

function readable(value: unknown): string {
  if (typeof value !== "string") return "";
  const text = value.trim();
  if (/^(会话\s*\d+|全自动目标任务|evolve-ab)$/.test(text)
    || /^(?:[a-z][a-z0-9_]*-)?[a-f\d]{6,}(?:[-_][a-z\d-]+)?$/i.test(text)
    || /^[a-f\d]{8}-[a-f\d]{4}-/i.test(text)) return "";
  return text;
}

function known<T extends object>(value: T | null | undefined): Partial<T> {
  return Object.fromEntries(Object.entries(value ?? {}).filter(([, item]) => item != null && item !== "")) as Partial<T>;
}

export function traceDisplayContext(run: TraceIdentityRun, fallback?: TraceDisplayContext): TraceDisplayContext {
  const snapshot = run.run_snapshot?.display_context as TraceDisplayContext | undefined;
  return { ...known(fallback), ...known(run.display_context), ...known(snapshot) };
}

/** A delayed snapshot must not erase a name already carried by the start event. */
export function mergeTraceDisplay<T extends TraceIdentityRun>(next: T, previous?: TraceIdentityRun): T {
  if (!previous || (next.trace_id && previous.trace_id && next.trace_id !== previous.trace_id)) return next;
  const nextContext = traceDisplayContext(next);
  const previousContext = traceDisplayContext(previous);
  const snapshotContext = {
    ...known(next.run_snapshot?.display_context as TraceDisplayContext | undefined),
    ...known(previous.run_snapshot?.display_context as TraceDisplayContext | undefined),
  };
  return {
    ...next,
    session_name: next.session_name || previous.session_name,
    started_at: next.started_at || previous.started_at,
    display_context: nextContext.legacy ? { ...nextContext, ...previousContext } : { ...previousContext, ...nextContext },
    run_snapshot: next.run_snapshot || previous.run_snapshot ? {
      ...previous.run_snapshot, ...next.run_snapshot,
      ...(Object.keys(snapshotContext).length ? { display_context: snapshotContext } : {}),
    } : undefined,
  };
}

export function getTraceDisplay(run: TraceIdentityRun, fallback?: TraceDisplayContext) {
  const context = traceDisplayContext(run, fallback);
  const purpose = run.run_purpose || run.purpose;
  const operation = context.operation || (purpose === "evolution_eval" ? "evaluation"
    : purpose === "evolution_evolve" ? "improvement" : undefined);
  const experiment = readable(context.experiment_name);
  const sample = readable(context.sample_name);
  const workspace = readable(context.workspace_title);
  const session = readable(run.session_name);
  const task = TASK_LABELS[context.task_type || context.target?.task_type || context.task_phase || ""] || "";
  const volumes = context.target?.volume_ids?.filter((number) => Number.isInteger(number) && number > 0) ?? [];
  const target = [volumes.length ? `第${volumes.join("、")}卷` : "",
    context.target?.chapter ? `第${context.target.chapter}章` : ""].filter(Boolean).join(" · ");
  const subtitle: string[] = [];
  let title: string;
  let missing = !!context.legacy;

  if (operation === "improvement") {
    title = [experiment || "实验", context.round != null ? `第${context.round}轮改版` : "候选改版"].join(" · ");
  } else if (experiment || sample || operation === "evaluation"
    || ["optimization", "evolution", "evolution_eval"].includes(purpose || "")) {
    const action = operation === "evaluation" ? "评分" : "生成";
    title = [sample || "实验运行", task + action].join(" · ");
    if (experiment) subtitle.push(experiment);
    if (!sample) missing = true;
  } else {
    const action = context.task_type === "character.generate" ? "人物生成"
      : context.task_type === "character.repair" ? "人物补卡"
      : context.task_type === "character_repair" ? "人物修复"
      : task ? task + "创作" : ({ creation: "创作", evidence_compile: "证据编译", evaluation: "评估", evolution: "进化" }[run.workload || ""]
      || (purpose === "user_generation" ? "用户创作" : ""));
    title = [workspace || session, target, action].filter(Boolean).join(" · ")
      || (run.status === "running" ? "运行信息同步中" : "历史运行");
    if (workspace && session && session !== workspace) subtitle.push(session);
    if (!workspace || !action) missing = true;
  }

  if (context.phase && PHASE_LABELS[context.phase]) subtitle.push(PHASE_LABELS[context.phase]);
  if (operation !== "improvement") {
    if (context.arm === "baseline") subtitle.push("固定原版");
    else if (context.arm === "candidate" && context.round != null) subtitle.push(`候选第${context.round}轮`);
    else if (context.round != null && context.round > 0) subtitle.push(`第${context.round}轮`);
    if (context.repeat != null && context.repeat > 0) subtitle.push(`第${context.repeat}次重复`);
  }
  const date = run.started_at ? new Date(run.started_at) : null;
  subtitle.push(date && !Number.isNaN(date.getTime()) ? date.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }) : "开始时间未记录");
  if (missing) subtitle.push("部分任务信息未记录");
  return { title, subtitle: subtitle.join(" · "), missing };
}
