/**
 * ArtifactPanel —— 五段产物面板（REQ-20261018-115508 FR-011）。
 *
 * 阶段条 + 选中产物查看（markdown 渲染）；正文文件进入人修编辑器
 * （ManuscriptEditor，FR-009；阅读/标注＋编辑双模式见 REQ-20261027-213246
 * DEC-005）。旧版工作区（五件套/旧七步/三步制）只读查看。
 *
 * 标注（REQ-20261027-213246）：五段制下渲染页可划词标注（AnnotationHost），
 * 顶部「标注 N 条」打开清单抽屉，「把标注交给陪练」跨产物打包走聊天通道。
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { markdownTableComponents } from "../../lib/markdown-components";
import type { OutlineFileEntry, PipelineStatus } from "../../lib/types";
import { annotationRelAllowed } from "@/lib/annotations";
import { useAnnotationsStore } from "@/stores/annotations";
import { useExecutionStore } from "@/stores/execution";
import { PipelineStageBar } from "./PipelineStageBar";
import { ManuscriptEditor } from "./ManuscriptEditor";
import { AnnotationHost } from "./annotations/AnnotationHost";
import { AnnotationDrawer } from "./annotations/AnnotationDrawer";
import { useWorkspaceStore } from "@/stores/workspace";
import { useStageSessionStore } from "@/stores/stage-session";

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
  const fiveKind = kind === "five";

  const annotations = useAnnotationsStore((s) => s.annotations);
  const pendingCount = annotations.filter((a) => a.status === "pending").length;

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [dispatching, setDispatching] = useState(false);
  const [reanchorTarget, setReanchorTarget] = useState<string | null>(null);

  // 产物树变化（轮询/watch 推送）时刷新标注状态——服务端读前做锚点复验
  const filesSignature = useMemo(
    () => files.map((f) => `${f.rel}:${f.markdown.length}`).join("|"),
    [files],
  );
  useEffect(() => {
    if (fiveKind && workspaceId) void useAnnotationsStore.getState().load(workspaceId);
  }, [fiveKind, workspaceId, filesSignature]);

  const handleDispatch = async () => {
    if (!useWorkspaceStore.getState().hasApiKey) {
      toast.error("请先在设置页填写你的 API Key，才能使用 AI 生成。");
      return;
    }
    setDispatching(true);
    try {
      const result = await useAnnotationsStore.getState().dispatch();
      const queued = useExecutionStore.getState().loading;
      void useExecutionStore.getState().submit(result.message);
      toast.success(queued ? "任务已排队：当前生成结束后自动发出" : "已交给陪练，处理过程见对话面板");
      if (result.invalidated > 0) {
        toast.info(`${result.invalidated} 条标注的原文已变化，转为已失效（不进任务包）`);
      }
      setDrawerOpen(false);
      useWorkspaceStore.setState({ activePanel: "chat" });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "发送失败，请重试");
    } finally {
      setDispatching(false);
    }
  };

  /** 重新框选（FR-002）：切到标注所属产物，下一次划选即换锚点。 */
  const handleReanchor = (annotationId: string) => {
    const ann = useAnnotationsStore.getState().annotations.find((a) => a.id === annotationId);
    if (!ann) return;
    if (!files.some((f) => f.rel === ann.artifact_rel)) {
      toast.error("该产物已不存在，请直接删除这条标注");
      return;
    }
    if (ann.artifact_rel !== activeRel) onSelect(ann.artifact_rel);
    setDrawerOpen(false);
    setReanchorTarget(annotationId);
    toast.info(`请在「${relLabel(ann.artifact_rel)}」里划选新的文字段落`);
  };

  return (
    <section className="panel-surface content-panel" aria-label="作品产物">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">Artifacts</span>
          <h2>作品产物</h2>
        </div>
        <div className="panel-heading-actions">
          {fiveKind ? (
            <button
              type="button"
              className={`panel-ann-entry${pendingCount > 0 ? " has-pending" : ""}`}
              onClick={() => setDrawerOpen(true)}
              title="查看全部标注；把标注交给陪练"
            >
              标注 {annotations.length} 条{pendingCount > 0 ? `（待处理 ${pendingCount}）` : ""}
            </button>
          ) : null}
          {/* 查看产物的显式回程：从对话跳过来后，「返回对话」在视线内，不必找回侧栏 */}
          <button
            type="button"
            className="panel-back-chat"
            onClick={() => useWorkspaceStore.setState({ activePanel: "chat" })}
          >
            ← 返回对话
          </button>
        </div>
      </div>

      <div className="content-panel-body">
        <PipelineStageBar
          pipeline={pipeline}
          fileRels={files.map((f) => f.rel)}
          onStageJump={(stage) => void useStageSessionStore.getState().jumpToStage(stage)}
        />

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
              <ManuscriptEditor
                workspaceId={workspaceId}
                chapter={chapter}
                rel={active.rel}
                reanchorTarget={reanchorTarget}
                onReanchorDone={() => setReanchorTarget(null)}
              />
            ) : (
              <div className="outline-markdown script-markdown">
                {active ? (
                  <AnnotationHost
                    rel={active.rel}
                    enabled={fiveKind && annotationRelAllowed(active.rel)}
                    markdown={active.markdown}
                    reanchorTarget={reanchorTarget}
                    onReanchorDone={() => setReanchorTarget(null)}
                  >
                    <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={markdownTableComponents}>
                      {active.markdown}
                    </ReactMarkdown>
                  </AnnotationHost>
                ) : null}
              </div>
            )}
          </>
        )}
      </div>

      <AnnotationDrawer
        open={drawerOpen}
        dispatching={dispatching}
        onClose={() => setDrawerOpen(false)}
        onDispatch={() => void handleDispatch()}
        onReanchor={handleReanchor}
      />
    </section>
  );
}
