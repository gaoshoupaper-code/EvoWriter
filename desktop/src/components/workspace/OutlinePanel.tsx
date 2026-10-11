import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { markdownTableComponents } from "../../lib/markdown-components";
import type { OutlineFileEntry, PipelineStatus } from "../../lib/types";
import { PipelineStageBar } from "./PipelineStageBar";

/**
 * 七步大纲面板（REQ-20261011-142529 FR-010，DEC-014 A 先行）——
 * 管线阶段条 + outline 产物文件切换 + markdown 渲染（含台账/章纲表格）。
 * 管理视图（台账看板/批进度）为下一迭代（DEC-014 B 后补）。
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
    <section className="panel-surface content-panel" aria-label="七步大纲">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">Pipeline</span>
          <h2>七步大纲</h2>
        </div>
      </div>

      <div className="content-panel-body">
        <PipelineStageBar pipeline={pipeline} fileRels={files.map((f) => f.rel)} />

        {kind === "legacy" ? (
          <p className="outline-state">旧版五件套工作区：七步产物为空，请切「大纲（旧）」面板查看。</p>
        ) : files.length === 0 ? (
          <p className="outline-state">管线尚未产出：从对话发起第一步「赛道确认」后，产物会出现在这里。</p>
        ) : (
          <>
            <div className="outline-file-tabs" role="tablist" aria-label="outline 产物">
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
