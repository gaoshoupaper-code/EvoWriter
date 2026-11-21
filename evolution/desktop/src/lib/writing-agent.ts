export const WRITING_AGENTS = {
  outline: { name: "总纲", task: "master_outline" },
  volume: { name: "卷纲", task: "volume_outlines" },
  chapter: { name: "章纲", task: "chapter_outlines" },
  text: { name: "正文", task: "chapter_text" },
} as const;

export type WritingAgent = keyof typeof WRITING_AGENTS;

const EVOLUTION_ASSETS_VIEW = "evolution-agent-assets";
export const EVOLUTION_AGENT_ASSETS_PATH = `/bench?view=${EVOLUTION_ASSETS_VIEW}`;

export function isEvolutionAgentAssetsView(query: URLSearchParams) {
  return query.get("view") === EVOLUTION_ASSETS_VIEW;
}

export const AREA_LABELS = {
  observation: "运行记录所属 Agent", evaluation: "评测对象", assets: "Harness 资产所属 Agent",
  evolution: "进化对象", versions: "版本所属 Agent", models: "模型配置所属 Agent",
} as const;
type Area = keyof typeof AREA_LABELS;

export function agentArea(path: string): Area | null {
  switch (path.split("/")[1]) {
    case "": case "analysis": case "traces": case "history": return "observation";
    case "bench": case "benchmark": case "dataset": return "evaluation";
    case "harness": return "assets";
    case "evolve": return "evolution";
    case "versions": return "versions";
    case "config": return "models";
    default: return null;
  }
}

export function isWritingAgent(value: string | null | undefined): value is WritingAgent {
  return !!value && Object.hasOwn(WRITING_AGENTS, value);
}

export function pageAgent(area: Area, search: string): WritingAgent {
  const explicit = new URLSearchParams(search).get("agent");
  if (isWritingAgent(explicit)) return explicit;
  try {
    const saved = localStorage.getItem("evolution.agent." + area);
    if (isWritingAgent(saved)) return saved;
  } catch { /* The URL still selects an agent when storage is unavailable. */ }
  return "outline";
}

export function rememberPageAgent(area: Area, agent: WritingAgent) {
  try { localStorage.setItem("evolution.agent." + area, agent); } catch { /* Optional preference. */ }
}

export function selectedAgent(hash = window.location.hash): WritingAgent | null {
  const [path, search = ""] = (hash.slice(1) || "/").split("#", 1)[0].split("?");
  const legacy = /^\/agents\/([^/]+)(?:\/|$)/.exec(path);
  if (legacy && isWritingAgent(legacy[1])) return legacy[1];
  const area = agentArea(path);
  return area ? pageAgent(area, search) : null;
}

export function withAgent(path: string, agent = selectedAgent()) {
  if (!agent) return path;
  const [base, search] = path.split("?");
  const params = new URLSearchParams(search);
  params.set("agent", agent);
  return base + "?" + params;
}

export function switchAgentPath(path: string, agent: WritingAgent) {
  // A private ID (or a candidate query) only resolves inside its original suite.
  if (path.startsWith("/traces/")) return withAgent("/", agent);
  if (path.startsWith("/bench/experiments/")) return withAgent("/bench/experiments", agent);
  if (path.startsWith("/evolve/")) return withAgent("/evolve", agent);
  return withAgent(path, agent);
}

export function currentTask() {
  return WRITING_AGENTS[selectedAgent() ?? "outline"].task;
}
