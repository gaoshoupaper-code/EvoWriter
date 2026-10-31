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
import { findArtifactGaps, redoEligibility, redoTargetKind, stageDisplayLabel } from "@/lib/artifact-redo";
import { deleteArtifactForRedo, deleteArtifactsBatch } from "@/lib/api";
import { refreshWorkspaceContent } from "@/lib/usePanelPolling";
import { useAnnotationsStore } from "@/stores/annotations";
import { useExecutionStore } from "@/stores/execution";
import { PipelineStageBar } from "./PipelineStageBar";
import { ManuscriptEditor } from "./ManuscriptEditor";
import { AnnotationHost } from "./annotations/AnnotationHost";
import { AnnotationDrawer } from "./annotations/AnnotationDrawer";
import { ConfirmDialog } from "./ConfirmDialog";
import { TrashDialog } from "./TrashDialog";
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
  // 删除重做（REQ-20261030-211446 全量开放：六类产物）；服务端做从下往上的权威校验
  const loading = useExecutionStore((s) => s.loading);
  const [redoOpen, setRedoOpen] = useState(false);
  const [redoBusy, setRedoBusy] = useState(false);
  const redo = useMemo(
    () => (active && fiveKind && redoTargetKind(active.rel) ? redoEligibility(active.rel, files) : null),
    [active, fiveKind, files],
  );
  // 批量删除（FR-005）：多选模式，仅同类可勾；后端逐个校验、部分成功
  const [batchMode, setBatchMode] = useState(false);
  const [batchSel, setBatchSel] = useState<Set<string>>(new Set());
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchBusy, setBatchBusy] = useState(false);
  const batchKind = useMemo(
    () => (batchSel.size ? redoTargetKind([...batchSel][0]) : null),
    [batchSel],
  );
  // 回收站（FR-006）
  const [trashOpen, setTrashOpen] = useState(false);
  // 缺号空档提示（FR-004：删哪补哪，只报内部洞）
  const gaps = useMemo(
    () => (fiveKind ? findArtifactGaps(files) : { outline: [], text: [] }),
    [files, fiveKind],
  );

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

  /** 删除重做确认后执行：删文件→回段重走，产物树与标注随刷新更新。 */
  const handleRedoConfirm = async () => {
    if (!active) return;
    setRedoBusy(true);
    try {
      const result = await deleteArtifactForRedo(workspaceId, active.rel);
      toast.success(`已删除「${relLabel(result.deleted)}」，回到${stageDisplayLabel(result.stage_label)}重做`);
      setRedoOpen(false);
      await refreshWorkspaceContent(workspaceId);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除失败，请重试");
    } finally {
      setRedoBusy(false);
    }
  };

  /** 多选勾选（FR-005）：首勾锁定同类，异类禁选；再点取消勾选。 */
  const toggleBatchSel = (rel: string) => {
    const kind = redoTargetKind(rel);
    if (!kind) return;
    setBatchSel((prev) => {
      const next = new Set(prev);
      if (next.has(rel)) {
        next.delete(rel);
        return next;
      }
      const lockKind = next.size ? redoTargetKind([...next][0]) : null;
      if (lockKind && lockKind !== kind) return prev;
      next.add(rel);
      return next;
    });
  };

  /** 批量删除确认后执行：逐个校验、部分成功；结果摘要进 toast。 */
  const handleBatchConfirm = async () => {
    const rels = [...batchSel];
    if (!rels.length) return;
    setBatchBusy(true);
    try {
      const result = await deleteArtifactsBatch(workspaceId, rels);
      if (result.deleted_count > 0) {
        toast.success(`已删除 ${result.deleted_count} 项，进入回收站`);
      }
      const failed = result.results.filter((r) => r.status !== "ok");
      if (failed.length) {
        toast.warning(`${failed.length} 项未删除：${failed[0].reason}${failed.length > 1 ? `（等 ${failed.length} 项）` : ""}`);
      }
      setBatchOpen(false);
      setBatchMode(false);
      setBatchSel(new Set());
      await refreshWorkspaceContent(workspaceId);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "批量删除失败，请重试");
    } finally {
      setBatchBusy(false);
    }
  };

  return (
    <section className="panel-surface content-panel" aria-label="作品产物">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">Artifacts</span>
          <h2>作品产物</h2>
        </div>
        <div className="panel-heading-actions">
          {redo && !batchMode ? (
            <button
              type="button"
              className={`panel-redo-entry${redo.eligible ? "" : " disabled"}`}
              disabled={!redo.eligible || loading || redoBusy}
              title={
                !redo.eligible
                  ? redo.reason
                  : loading
                    ? "生成中不可删除，等本轮结束再操作"
                    : "删除该定稿产物并回到对应段重做（二次确认）"
              }
              onClick={() => setRedoOpen(true)}
            >
              删除重做
            </button>
          ) : null}
          {fiveKind && files.length > 0 && !batchMode ? (
            <button
              type="button"
              className="panel-batch-entry"
              disabled={loading}
              title={loading ? "生成中不可批量删除，等本轮结束再操作" : "勾选多个同类产物一次删除（仍逐个校验资格）"}
              onClick={() => {
                setBatchSel(new Set());
                setBatchMode(true);
              }}
            >
              批量删除
            </button>
          ) : null}
          {fiveKind && !batchMode ? (
            <button
              type="button"
              className="panel-trash-entry"
              onClick={() => setTrashOpen(true)}
              title="查看删除重做移入回收站的产物，可恢复回原位"
            >
              回收站
            </button>
          ) : null}
          {fiveKind && !batchMode ? (
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
              {files.map((f) => {
                const fileKind = redoTargetKind(f.rel);
                const selected = batchSel.has(f.rel);
                const locked = batchMode && batchKind !== null && fileKind !== batchKind;
                return (
                  <button
                    key={f.rel}
                    type="button"
                    role="tab"
                    aria-selected={!batchMode && f.rel === active?.rel}
                    className={
                      batchMode
                        ? `outline-file-tab batch${selected ? " picked" : ""}${locked ? " locked" : ""}`
                        : `outline-file-tab${f.rel === active?.rel ? " active" : ""}`
                    }
                    disabled={locked}
                    title={locked ? "只能批量勾选同类产物（先勾的是其他类型）" : undefined}
                    onClick={() => (batchMode ? toggleBatchSel(f.rel) : onSelect(f.rel))}
                  >
                    {batchMode ? `${selected ? "✓ " : ""}${relLabel(f.rel)}` : relLabel(f.rel)}
                  </button>
                );
              })}
            </div>
            {batchMode ? (
              <div className="batch-bar">
                <span className="batch-bar-count">
                  已选 {batchSel.size} 项{batchKind ? `（${batchKind === "theme" ? "主题" : batchKind === "outline" ? "总纲" : batchKind === "volume" ? "卷纲" : batchKind === "chapter" ? "章纲" : batchKind === "style" ? "文风" : batchKind === "character" ? "人物" : "正文"}）` : ""}
                </span>
                <button
                  type="button"
                  className="panel-redo-entry"
                  disabled={batchSel.size === 0 || loading || batchBusy}
                  onClick={() => setBatchOpen(true)}
                >
                  删除所选
                </button>
                <button
                  type="button"
                  className="panel-trash-entry"
                  disabled={batchBusy}
                  onClick={() => {
                    setBatchMode(false);
                    setBatchSel(new Set());
                  }}
                >
                  退出多选
                </button>
              </div>
            ) : gaps.outline.length || gaps.text.length ? (
              <p className="outline-gap-hint">
                {[
                  gaps.outline.length ? `章纲缺第 ${gaps.outline.join("、")} 章` : "",
                  gaps.text.length ? `正文缺第 ${gaps.text.join("、")} 章` : "",
                ].filter(Boolean).join("；")}
                ——删除重做留下的空档，生成时会按缺号补写
              </p>
            ) : null}
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

      <ConfirmDialog
        open={redoOpen}
        title={`删除重做「${active ? relLabel(active.rel) : ""}」？`}
        description={
          "该定稿产物会被删除并移入回收站（可在「回收站」恢复），" +
          "流水线回到对应段从头重做；该产物上的标注一并清除。下游产物不会被连带删除。"
        }
        confirmLabel="删除重做"
        loading={redoBusy}
        onConfirm={() => void handleRedoConfirm()}
        onCancel={() => setRedoOpen(false)}
      />

      <ConfirmDialog
        open={batchOpen}
        title={`批量删除重做所选 ${batchSel.size} 项？`}
        description={
          "所选同类产物会逐个删除并移入回收站（可在「回收站」恢复）。" +
          "每一项单独校验资格：直接下游仍在的项会被跳过并在结果里说明原因；下游产物不会被连带删除。"
        }
        confirmLabel="删除所选"
        loading={batchBusy}
        onConfirm={() => void handleBatchConfirm()}
        onCancel={() => setBatchOpen(false)}
      />

      <TrashDialog open={trashOpen} workspaceId={workspaceId} onClose={() => setTrashOpen(false)} />
    </section>
  );
}
