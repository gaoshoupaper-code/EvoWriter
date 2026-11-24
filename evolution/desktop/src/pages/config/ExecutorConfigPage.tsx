import PageAgentSelector from "@/components/PageAgentSelector";
import LlmConfigPanel from "./LlmConfigPanel";

/**
 * 执行端模型配置页（scope 分家 2026-07-18，新建）。
 *
 * executor 给用户写正文时调用的大模型配置。
 * 与"进化端模型"独立——可使用不同厂商/模型，各自维护激活项。
 */
export default function ExecutorConfigPage() {
  return (
    <>
      <header className="page-header">
        <h1>执行端模型</h1>
        <p className="page-desc">
          评测工作台用本 Agent 选中的执行模型生成文稿。
          写作默认使用激活项，更新约 60 秒内生效。
        </p>
      </header>
      <PageAgentSelector />
      <LlmConfigPanel scope="executor" />
    </>
  );
}
