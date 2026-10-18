/**
 * StageTransitionCard —— 阶段完成卡片（REQ-20261018-175526 FR-004/006，DEC-003/012）。
 *
 * 挂起于旧对话末尾的缓冲：阶段已变、旧对话保持可读可滚，用户点主按钮
 * 「进入X」才归档旧会话、开新会话、清空页面（DEC-003）；直接打字发送 =
 * 隐式切换（DEC-012，发送前由 execution 侧先走 confirmTransition）。
 *
 * 跳段后退（章纲↔正文往返 / 下游申请改上游）时标注「应 X 阶段申请」（FR-006）。
 */
import { useContentStore } from "@/stores/content";
import { useWorkspaceStore } from "@/stores/workspace";
import { useStageSessionStore } from "@/stores/stage-session";
import { stageLabel } from "@/lib/pipeline-steps";

/** 阶段 → 产物目录前缀（查看产物入口用；theme/outline 是根级文件） */
const STAGE_RELS: Record<string, string[]> = {
  theme: ["主题.md"],
  outline: ["总纲.md"],
  volume: ["卷纲/"],
  chapter: ["章纲/"],
  text: ["正文/"],
};

function label(stage: string): string {
  return stageLabel(stage) ?? stage;
}

/** 次链接「查看产物」：选中上一阶段的最新产物并切到产物面板 */
function showFromStageArtifacts(fromStage: string) {
  const content = useContentStore.getState();
  const rels = STAGE_RELS[fromStage] ?? [];
  const hit = [...content.outlineFiles].reverse().find((f) => rels.some((r) => f.rel === r || f.rel.startsWith(r)));
  if (hit) content.setActiveOutlineRel(hit.rel);
  useWorkspaceStore.setState({ activePanel: "artifacts" });
}

export function StageTransitionCard({ onOpenStageHistory }: { onOpenStageHistory: (stage: string) => void }) {
  const pending = useStageSessionStore((s) => s.pendingTransition);
  if (!pending) return null;

  const toName = label(pending.toStage);
  const fromName = label(pending.fromStage);
  const headline = pending.sourceStage
    ? `应${label(pending.sourceStage)}申请，回到${toName}`
    : `${fromName}完成`;

  return (
    <div className="stage-transition-card" role="status" data-testid="stage-transition-card">
      <div className="stage-transition-head">
        <span className="stage-transition-check">✅</span>
        <span className="stage-transition-title">{headline}</span>
      </div>
      <button
        type="button"
        className="stage-transition-primary"
        onClick={() => void useStageSessionStore.getState().confirmTransition()}
      >
        进入{toName}
      </button>
      <div className="stage-transition-links">
        <button type="button" onClick={() => showFromStageArtifacts(pending.fromStage)}>查看{fromName}产物</button>
        <span aria-hidden="true">·</span>
        <button type="button" onClick={() => onOpenStageHistory(pending.fromStage)}>翻{fromName}历史</button>
      </div>
    </div>
  );
}
