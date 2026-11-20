import type { HarnessElementView, AgentDiff } from "@/lib/api";
import { MiddlewareLifecycle } from "./MiddlewareLifecycle";

export function MiddlewareTab({
  agents,
  diffs,
  hasSource,
}: {
  agents: HarnessElementView[];
  diffs: Map<string, AgentDiff> | null;
  hasSource: boolean;
}) {
  if (!agents.length) return <p className="tool-empty">当前视图没有可查看的角色。</p>;

  return agents.map((agent) => (
    <MiddlewareLifecycle
      key={agent.name}
      agent={agent}
      diff={diffs?.get(agent.name)}
      hasSource={hasSource}
      showAgentName={agents.length > 1}
    />
  ));
}
