export const WRITING_AGENTS = {
  outline: { name: "总纲", task: "master_outline" },
  volume: { name: "卷纲", task: "volume_outlines" },
  chapter: { name: "章纲", task: "chapter_outlines" },
  text: { name: "正文", task: "chapter_text" },
} as const;

export type WritingAgent = keyof typeof WRITING_AGENTS;

export function selectedAgent(hash = window.location.hash): WritingAgent | null {
  const value = hash.match(/^#\/agents\/([^/?]+)(?:\/|$)/)?.[1];
  return value && Object.hasOwn(WRITING_AGENTS, value) ? value as WritingAgent : null;
}

export function currentTask() {
  return WRITING_AGENTS[selectedAgent() ?? "outline"].task;
}
