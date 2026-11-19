import PageAgentSelector from "@/components/PageAgentSelector";
import { StageRules } from "@/components/bench/StageRules";

export default function BenchRules() {
  return <div className="bench-page"><h1>评测规则</h1><PageAgentSelector /><StageRules /></div>;
}
