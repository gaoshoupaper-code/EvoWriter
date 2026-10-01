import type { EvolveAgent } from "@/lib/api";

/**
 * 工作台空态卡（无选中会话时，REQ-20261001-131018 交互修正 2026-10-01）。
 *
 * 工作台常驻显示当前（最新）会话；本卡只在「没有可显示的会话」时出现，
 * 引导用户开第一个会话——「开新会话」统一走 NewSessionDialog（独立组件，
 * 工具栏常驻可点，不依赖工作台空闲）。
 */
interface Props {
  agents: EvolveAgent[];
  hasAnySession: boolean;
  selectedAgentName: string | null;
  onOpenNewSession: () => void;
}

export default function AgentLaunchCard({
  agents,
  hasAnySession,
  selectedAgentName,
  onOpenNewSession,
}: Props) {
  return (
    <section className="conversation-panel idle">
      <div className="start-card">
        <div className="start-icon">🧬</div>
        <h2 className="start-title">
          {hasAnySession ? "当前没有选中的会话" : "开始第一次进化共创"}
        </h2>
        <p className="start-subtitle">
          {hasAnySession
            ? selectedAgentName
              ? `「${selectedAgentName}」名下暂无会话。开一个新会话，或去「进化历史」回看其他 Agent 的会话。`
              : "去「进化历史」回看以前的会话，或开一个新会话。"
            : "进化 Agent 绑定一个作品：它用工具按需翻看该作品的全部会话记录与产物，结合 harness 结构与运作证据，和你讨论怎么改进创作 Agent。"}
        </p>
        <div className="start-form">
          <button type="button" className="start-btn" onClick={onOpenNewSession}>
            ＋ 新开会话
          </button>
        </div>
        {agents.length === 0 && (
          <p className="start-hint">还没有进化 Agent——新开会话时可以先创建（选作品绑定）。</p>
        )}
      </div>
    </section>
  );
}
