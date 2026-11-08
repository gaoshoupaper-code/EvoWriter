import { EvolutionExperiments } from "@/components/bench/EvolutionExperiments";

export default function BenchWorkbench() {
  return <div className="bench-page bench-workbench">
    <header className="page-header"><h1>评测工作台</h1>
      <p className="page-desc">启动实验，掌握当前进度。运行过程、评分和文稿统一保存在实验记录。</p></header>
    <EvolutionExperiments />
  </div>;
}
