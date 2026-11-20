import type { HarnessElementView, AgentDiff } from "@/lib/api";
import { HOOK_ORDER, agentLabel } from "@/lib/harness-constants";
import { MiddlewareNode } from "./MiddlewareNode";

export function MiddlewareLifecycle({
  agent,
  diff,
  showAgentName,
  hasSource,
}: {
  agent: HarnessElementView;
  diff?: AgentDiff;
  showAgentName: boolean;
  hasSource: boolean;
}) {
  const middlewares = agent.middlewares.map((mw) => ({
    mw,
    hooks: HOOK_ORDER.filter((hook) => mw.hooks?.includes(hook) ?? mw.hook === hook),
  }));
  const unknown = middlewares.filter(({ hooks }) => !hooks.length).map(({ mw }) => mw.class_name);
  const incomplete = agent.middleware_available === false;

  return (
    <div className="mw-lifecycle">
      {showAgentName && <h2 className="mw-agent-title">{agent.display_name ?? agentLabel(agent.name)}</h2>}
      {(!hasSource || incomplete || unknown.length > 0) && (
        <p className="mw-lifecycle-warning">
          {unknown.length > 0
            ? `此版本钩子数据不足，无法确定位置：${unknown.join("、")}`
            : "此版本钩子数据不足，无法确认当前角色的中间件。"}
        </p>
      )}
      {HOOK_ORDER.map((hook) => (
        <section key={hook} className="mw-hook">
          <h3 className="mw-hook-title">{hook}</h3>
          {middlewares.filter(({ hooks }) => hooks.includes(hook)).map(({ mw }, index) => (
            <MiddlewareNode
              key={`${mw.class_name}-${index}`}
              mw={mw}
              change={diff?.processors?.find((change) => (
                change.class_change.new === mw.class_name && change.key.group === mw.group
              ))}
              activeHook={hook}
            />
          ))}
        </section>
      ))}
    </div>
  );
}
