/**
 * StageGuide —— 新会话空页面的阶段引导语（REQ-20261018-175526 FR-003，DEC-008）。
 *
 * 显示条件（ChatPanel 控制）：当前视图无 user 消息且不在生成中——引导语随
 * 首条消息出现后消失。五段各一句引导 + 最多两个可点示例问题（点击填入输入框）。
 * 非五段阶段回退通用引导（FR-003 失败语义：不显示空白）。
 */
import { stageGuide } from "@/lib/pipeline-steps";

const FALLBACK_TEXT = "和你的创作伙伴聊聊吧";

type StageGuideProps = {
  phase: string;
  onPickSuggestion: (text: string) => void;
};

export function StageGuide({ phase, onPickSuggestion }: StageGuideProps) {
  const guide = stageGuide(phase);
  return (
    <div className="stage-guide" data-testid="stage-guide">
      <p className="stage-guide-text">{guide?.text ?? FALLBACK_TEXT}</p>
      {guide ? (
        <div className="stage-guide-suggestions">
          {guide.suggestions.map((s) => (
            <button type="button" key={s} className="stage-guide-chip" onClick={() => onPickSuggestion(s)}>
              {s}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
