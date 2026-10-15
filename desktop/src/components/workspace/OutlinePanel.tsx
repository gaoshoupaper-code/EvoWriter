import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { markdownTableComponents } from "../../lib/markdown-components";
import type { OutlineFileEntry, PipelineStatus } from "../../lib/types";
import { PipelineStageBar } from "./PipelineStageBar";

/**
 * 三步大纲面板（REQ-20261015-161200 FR-008）——
 * 管线阶段条 + outline 产物文件切换 + markdown 渲染（含台账/章纲表格）。
 * 旧版工作区（五件套/旧七步）：只读查看，无修订入口。
 */
type OutlinePanelProps = {
  kind: string;
  pipeline: PipelineStatus | null;
  files: OutlineFileEntry[];
  activeRel: string;
  onSelect: (rel: string) => void;
};

function relLabel(rel: string): string {
  return rel.replace(/^outline\//, "").replace(/\.md$/, "");
}

export function OutlinePanel({ kind, pipeline, files, activeRel, onSelect }: OutlinePanelProps) {
  const active = files.find((f) => f.rel === activeRel) ?? files[0] ?? null;

  return (
    <section className="panel-surface content-panel" aria-label="大纲">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">Outline</span>
          <h2>大纲</h2>
        </div>
      </div>

      <div className="content-panel-body">
        <PipelineStageBar pipeline={pipeline} fileRels={files.map((f) => f.rel)} />

        {kind === "legacy" && files.length === 0 ? (
          <p className="outline-state">旧版工作区：内容走旧面板，此处只读。</p>
        ) : files.length === 0 ? (
          <p className="outline-state">还没开始：从对话发起第一步后，大纲会出现在这里。</p>
        ) : (
          <>
            <div className="outline-file-tabs" role="tablist" aria-label="大纲文件">
              {files.map((f) => (
                <button
                  key={f.rel}
                  type="button"
                  role="tab"
                  aria-selected={f.rel === active?.rel}
                  className={`outline-file-tab${f.rel === active?.rel ? " active" : ""}`}
                  onClick={() => onSelect(f.rel)}
                >
                  {relLabel(f.rel)}
                </button>
              ))}
            </div>
            <div className="outline-markdown script-markdown">
              {active ? (
                <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={markdownTableComponents}>
                  {active.markdown}
                </ReactMarkdown>
              ) : null}
            </div>
          </>
        )}
      </div>
    </section>
  );
}
