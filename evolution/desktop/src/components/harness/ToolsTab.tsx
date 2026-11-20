import type { HarnessElementsView, HarnessElementView, ToolInfo } from "@/lib/api";

export function ToolsTab({
  tools,
  agents,
  layout,
}: {
  tools: ToolInfo[];
  agents: HarnessElementView[];
  layout?: HarnessElementsView["layout"];
}) {
  if (!agents.length) return <p className="tool-empty">当前视图没有可查看的角色。</p>;
  if (layout !== "manifest" || agents.some((agent) => !agent.tools)) {
    return <p className="tool-empty">此版本数据不足，无法确认当前角色的工具名单。</p>;
  }

  const names = [...new Set(agents.flatMap((agent) => agent.tools ?? []))];
  if (!names.length) return <p className="tool-empty">当前角色未配置工具。</p>;

  const byPath = new Map(tools.map((tool) => [tool.path, tool]));
  return (
    <ul className="tool-list">
      {names.map((name) => {
        const description = byPath.get(`tools/${name}.py`)?.description;
        return (
          <li key={name} className="tool-row">
            <code className="tool-name">{name}</code>
            <span className="tool-desc">{description || "用途资料不足。"}</span>
          </li>
        );
      })}
    </ul>
  );
}
