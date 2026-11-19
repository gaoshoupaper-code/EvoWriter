import { createContext, useContext } from "react";
import { Link } from "react-router-dom";
import { AREA_LABELS, switchAgentPath, WRITING_AGENTS, type WritingAgent } from "@/lib/writing-agent";

export const PageAgentContext = createContext<{
  area: keyof typeof AREA_LABELS | null;
  agent: WritingAgent | null;
  pathname: string;
  returnTo: string;
} | null>(null);

export default function PageAgentSelector() {
  const scope = useContext(PageAgentContext);
  if (!scope?.area || !scope.agent) return null;
  const label = AREA_LABELS[scope.area];
  return (
    <nav className="page-agent-nav" aria-label={label}>
      <span>{label}</span>
      {(Object.keys(WRITING_AGENTS) as WritingAgent[]).map(key => (
        <Link key={key} to={key === scope.agent ? scope.returnTo : switchAgentPath(scope.pathname, key)} aria-current={key === scope.agent ? "true" : undefined}>
          {WRITING_AGENTS[key].name}
        </Link>
      ))}
    </nav>
  );
}
