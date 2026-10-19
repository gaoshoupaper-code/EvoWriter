/**
 * ArtifactPanel —— 五段产物面板（REQ-20261018-115508 FR-011）。
 *
 * 阶段条 + 选中产物查看（markdown 渲染）；正文文件进入人修编辑器
 * （ManuscriptEditor，FR-009）。旧版工作区（五件套/旧七步/三步制）只读查看。
 */
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { markdownTableComponents } from "../../lib/markdown-components";
import type { OutlineFileEntry, PipelineStatus } from "../../lib/types";
import { PipelineStageBar } from "./PipelineStageBar";
import { ManuscriptEditor } from "./ManuscriptEditor";
import { useWorkspaceStore } from "@/stores/workspace";

type ArtifactPanelProps = {
  kind: string;
  pipeline: PipelineStatus | null;
  files: OutlineFileEntry[];
  activeRel: string;
  onSelect: (rel: string) => void;
  workspaceId: string;
};

function relLabel(rel: string): string {
  return rel.replace(/\.md$/, "");
}

/** 正文 rel → 章号（正文/第007章一百级回家.md → 7） */
function chapterOfTextRel(rel: string): number | null {
  const m = /^正文\/第(\d+)章.*\.md$/.exec(rel);
  return m ? Number(m[1]) : null;
}

export function ArtifactPanel({
  kind,
  pipeline,
  files,
  activeRel,
  onSelect,
  workspaceId,
}: ArtifactPanelProps) {
  const active = files.find((f) => f.rel === activeRel) ?? files[0] ?? null;
  const chapter = active ? chapterOfTextRel(active.rel) : null;
  const isManuscript = chapter !== null;

  return (
    <section className="panel-surface content-panel" aria-label="作品产物">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">Artifacts</span>
          <h2>作品产物</h2>
        </div>
        {/* 查看产物的显式回程：从对话跳过来后，「返回对话」在视线内，不必找回侧栏 */}
        <button
          type="button"
          className="panel-back-chat"
          onClick={() => useWorkspaceStore.setState({ activePanel: "chat" })}
        >
          ← 返回对话
        </button>
      </div>

      <div className="content-panel-body">
        <PipelineStageBar pipeline={pipeline} fileRels={files.map((f) => f.rel)} />

        {kind === "legacy" && files.length === 0 ? (
          <p className="outline-state">旧版工作区：内容走旧面板数据，此处只读。</p>
        ) : files.length === 0 ? (
          <p className="outline-state">还没开始：从对话发起第一步后，产物会出现在这里。</p>
        ) : (
          <>
            <div className="outline-file-tabs" role="tablist" aria-label="产物文件">
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
            {active && isManuscript && kind === "five" ? (
              <ManuscriptEditor workspaceId={workspaceId} chapter={chapter} />
            ) : (
              <div className="outline-markdown script-markdown">
                {active ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={markdownTableComponents}>
                    {active.markdown}
                  </ReactMarkdown>
                ) : null}
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
