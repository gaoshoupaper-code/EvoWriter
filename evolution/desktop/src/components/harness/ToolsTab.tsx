import { Fragment } from "react";
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
        const tool = byPath.get(`tools/${name}.py`);
        const description = tool?.description;
        // 显示的是模型实际读到的说明全文；换行写进 DOM（<br>），不依赖样式就逐行可读（REQ-20261125-210621 FR-003）。
        const lines = description?.split("\n") ?? [];
        return (
          <li key={name} className="tool-row">
            <code className="tool-name">{name}</code>
            <span className="tool-desc">
              {description
                ? lines.map((line, index) => (
                    <Fragment key={index}>
                      {index > 0 && <br />}
                      {line}
                    </Fragment>
                  ))
                : "用途资料不足。"}
              {description && tool?.description_source === "fallback" && (
                <span className="tool-desc-note">未读到源码里的说明</span>
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
