/**
 * 小衍人格文案库
 *
 * 所有执行体验中的拟人化文案集中管理。调性：活泼陪伴（执行中）→
 * 中性清晰（HITL 提问）→ 庆祝（交付）→ 温暖（失败/停止）。
 *
 * 使用方式：各 ExecutionView 子视图按 phase/stage 取文案。
 */

// ── 阶段人话名（与 stage.ts 的 StageType 对应）──
export const STAGE_DISPLAY_NAMES: Record<string, string> = {
  storybuilding: "构思故事",
  review: "审查修订",
  general: "收尾整理",
};

// ── 黑屏期（booting）文案 ──
export const BOOTING_COPY = [
  "好的，我先理一下思路...",
  "让我想想这个故事怎么展开...",
  "准备中，马上开始...",
];

// ── 思考态（thinking）文案：信号驱动 + 主题池轮播（FR-004/005/007）──

/** 主题池键（THINKING_COPY 的键） */
export type ThinkingTheme = "storybuilding" | "detailOutline" | "writing" | "meta" | "review" | "general" | "image";

/** 工具/子 Agent 信号 → 主题池键（FR-004 映射表） */
export const TOOL_THEME_KEYS: Record<string, ThinkingTheme> = {
  storybuilding: "storybuilding",
  "detail-outline": "detailOutline",
  writing: "writing",
  meta: "meta",
};

/** 工具/子 Agent 人话名（降级文案与步骤列表共用） */
export const TOOL_DISPLAY_NAMES: Record<string, string> = {
  task: "派发任务",
  set_goal: "设定目标",
  record_goal_completion: "记录目标",
  storybuilding: "构思故事",
  "detail-outline": "细纲",
  writing: "正文写作",
  meta: "收尾整理",
};

/** 轮播节奏：同主题超时换句；不重复最近 N 句（池不足 3 句时放宽为 1） */
export const THINKING_ROTATE_MS = 7000;
export const THINKING_NO_REPEAT = 2;

export const THINKING_COPY: Record<ThinkingTheme, string[]> = {
  storybuilding: [
    "正在搭故事的骨架和核心人物...",
    "想想主角和世界观的设定...",
    "梳理一下故事的核心冲突...",
    "正在设计故事的转折点...",
    "把主线脉络再理一遍...",
  ],
  detailOutline: [
    "正在把大纲拆成一场一场的戏...",
    "细化每个场景的节拍...",
    "核对场景之间的衔接...",
    "正在安排每一幕的节奏...",
  ],
  writing: [
    "正在写正文，这段有点长，稍等我...",
    "笔下的人物正在对话...",
    "正在打磨这一段的细节...",
    "文思如泉涌，停不下来...",
    "正在给画面补上光影...",
  ],
  meta: [
    "正在整理产物和收尾...",
    "做最后的检查和润色...",
    "把成果归置整齐...",
  ],
  review: [
    "正在审查故事线的一致性...",
    "核对事件的时序和归属...",
  ],
  general: [
    "整理一下收尾工作...",
    "做最后的检查和润色...",
    "马上就好...",
    "正在核对产出...",
  ],
  image: [
    "正在构思画面...",
    "调整构图和光影...",
    "正在给画面上色...",
    "细化主体和背景...",
  ],
};

/** 思考态文案的信号输入 */
export interface ThinkingSignals {
  toolName?: string;
  subagentType?: string;
  streamKind?: "" | "writing" | "image";
}

/**
 * 信号 → 主题池键。返回 "" 表示无映射（调用方降级显示工具人话名）。
 * 图片流优先：streamKind=image 时无视工具信号（图片流无 tool_call 事件）。
 */
export function resolveThinkingTheme(signals: ThinkingSignals): ThinkingTheme | "" {
  if (signals.streamKind === "image") return "image";
  const key = TOOL_THEME_KEYS[signals.subagentType ?? ""] ?? TOOL_THEME_KEYS[signals.toolName ?? ""];
  return key ?? "";
}

/**
 * 从主题池取一条文案，排除刚用过的（FR-005 防重复）。
 * recent 是最近展示过的文案（新的在后）。
 */
export function pickThinkingCopy(theme: ThinkingTheme | "", recent: string[] = []): string {
  const pool = (theme && THINKING_COPY[theme]) || THINKING_COPY.general;
  const excludeSize = pool.length >= 3 ? THINKING_NO_REPEAT : Math.min(1, pool.length - 1);
  const excludes = recent.slice(-excludeSize);
  const candidates = pool.filter((c) => !excludes.includes(c));
  const chosen = candidates.length > 0 ? candidates[Math.floor(Math.random() * candidates.length)] : pool[0];
  return chosen;
}

// ── 交付仪式（delivering）文案 ──
export const DELIVERY_COPY = [
  "写好啦！你看看怎么样～",
  "搞定！这次写了不少呢～",
  "完成啦！希望你喜欢～",
  "交稿！有要改的地方随时说～",
];

export const DELIVERY_INTERACTION = "你觉得怎么样？要改的话跟我说";

// ── HITL 提问（asking）引导语 ──
export const ASKING_INTRO = [
  "这里需要你拍个板——",
  "有个地方想问问你——",
  "这里我有点纠结——",
];

// ── 失败态（failed）文案 ──
export const FAILED_COPY: Record<string, string> = {
  heartbeat_timeout: "抱歉，连接好像断了...",
  credit_exhausted: "积分不够了，需要补充一下...",
  default: "抱歉，刚才出了点问题...",
};

export const FAILED_ACTION = "要我再试一次吗？";

// ── 停止态（stopped）文案 ──
export const STOPPED_COPY = "好的，停下了。";
export const STOPPED_ACTION = "要继续的话，点这里就好";

// ── 多轮记忆感（resume 时第二次发起）文案 ──
export const MEMORY_COPY = [
  "好的，接着上次的故事...",
  "嗯，我们继续——",
  "好嘞，接着来！",
];

// ── 工具函数：从数组中按某种策略取文案 ──

/** 随机取一条（不重复上次，如果可能） */
export function pickRandom(pool: string[], lastIndex: number = -1): { text: string; index: number } {
  if (pool.length === 1) return { text: pool[0], index: 0 };
  let idx = lastIndex;
  while (idx === lastIndex) {
    idx = Math.floor(Math.random() * pool.length);
  }
  return { text: pool[idx], index: idx };
}

/** 按 stage type 取阶段人话名 */
export function getStageDisplayName(stageType: string | undefined): string {
  if (!stageType) return "执行中";
  return STAGE_DISPLAY_NAMES[stageType] ?? "执行中";
}

/**
 * 把错误类型映射成人话失败文案。
 * errMsg 是 performSubmit catch 里的原始 error message。
 */
export function getFailedCopy(errMsg: string): string {
  if (errMsg.includes("HEARTBEAT_TIMEOUT") || errMsg.includes("连接已断开")) return FAILED_COPY.heartbeat_timeout;
  if (errMsg.includes("积分") || errMsg.includes("403") || errMsg.includes("冻结")) return FAILED_COPY.credit_exhausted;
  return FAILED_COPY.default;
}
