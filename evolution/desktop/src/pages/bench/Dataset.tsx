import PageAgentSelector from "@/components/PageAgentSelector";
import { StagePackages } from "@/components/bench/StagePackages";

export default function BenchDataset() {
  return <div className="bench-page"><h1>评测数据集</h1><PageAgentSelector /><StagePackages /></div>;
}
