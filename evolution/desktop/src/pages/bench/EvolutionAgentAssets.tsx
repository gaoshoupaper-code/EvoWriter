import { useEffect, useState } from "react";
import PageAgentSelector from "@/components/PageAgentSelector";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { getExperimentEvolverAssets, type ExperimentEvolverAssets } from "@/lib/api";
import { EVOLUTION_AGENT_ASSETS_PATH } from "@/lib/writing-agent";

export default function EvolutionAgentAssets() {
  const [result, setResult] = useState<ExperimentEvolverAssets | Error | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    void getExperimentEvolverAssets().then(
      (assets) => { if (current) setResult(assets); },
      (error: unknown) => { if (current) setResult(error instanceof Error ? error : new Error("无法加载资产")); },
    );
    return () => { current = false; };
  }, [attempt]);

  function retry() {
    setResult(null);
    setAttempt((previous) => previous + 1);
  }

  return <div className="harness-page">
    <header className="page-header">
      <h1>进化 Agent 资产</h1>
      <p className="page-desc">查看评测后负责归因和改版的 Agent 当前使用的配置。</p>
      <PageAgentSelector switchPath={EVOLUTION_AGENT_ASSETS_PATH} />
    </header>
    {result === null ? <div className="page-loading" role="status">正在加载进化 Agent 资产…</div>
      : result instanceof Error ? <div className="evo-state-error" role="alert">
        <p>加载进化 Agent 资产失败。{result.message}</p>
        <button type="button" className="evo-retry-button" onClick={retry}>重试</button>
      </div>
      : <Tabs defaultValue="prompt" className="harness-tabs">
        <TabsList aria-label="进化 Agent 资产类型">
          <TabsTrigger value="prompt">提示词</TabsTrigger>
          <TabsTrigger value="tools">工具</TabsTrigger>
        </TabsList>
        <TabsContent value="prompt">
          <pre className="prompt-full" tabIndex={0}>{result.system_prompt}</pre>
        </TabsContent>
        <TabsContent value="tools">
          <ul className="tool-list">
            {result.tools.map(({ name, description }) => <li className="tool-row" key={name}>
              <span className="tool-name">{name}</span>
              <span className="tool-desc">{description}</span>
            </li>)}
          </ul>
        </TabsContent>
      </Tabs>}
  </div>;
}
