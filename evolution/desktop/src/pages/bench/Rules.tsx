import { useOutletContext, useSearchParams } from "react-router-dom";
import PageAgentSelector from "@/components/PageAgentSelector";
import { StageRules } from "@/components/bench/StageRules";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { JudgeGroupPanel } from "@/pages/config/JudgeGroupPage";

export default function BenchRules() {
  const canManageGroups = useOutletContext<{ isSuperAdmin: boolean } | null>()?.isSuperAdmin;
  const [query, setQuery] = useSearchParams();
  const groupsSelected = canManageGroups && query.get("tab") === "judge-groups";

  function selectTab(tab: string) {
    const next = new URLSearchParams(query);
    if (tab === "judge-groups") next.set("tab", tab);
    else next.delete("tab");
    setQuery(next);
  }

  return <div className="bench-page">
    <h1>评测规则</h1>
    <PageAgentSelector switchPath={groupsSelected ? "/bench/rules?tab=judge-groups" : undefined} />
    {canManageGroups ? <Tabs value={groupsSelected ? "judge-groups" : "rules"} onValueChange={selectTab}>
      <TabsList className="evaluation-rules-tabs" aria-label="评测规则设置">
        <TabsTrigger value="rules">评分规则</TabsTrigger>
        <TabsTrigger value="judge-groups">评测组</TabsTrigger>
      </TabsList>
      <TabsContent value="rules" className="mt-4"><StageRules /></TabsContent>
      <TabsContent value="judge-groups" className="mt-4"><JudgeGroupPanel /></TabsContent>
    </Tabs> : <StageRules />}
  </div>;
}
