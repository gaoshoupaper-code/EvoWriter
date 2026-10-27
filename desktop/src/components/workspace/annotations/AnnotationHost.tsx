/**
 * AnnotationHost —— 产物划词标注宿主（REQ-20261027-213246 FR-001/002 UI）。
 *
 * 包在渲染态 markdown 外层。交互（含 2026-10-27 修订）：
 * - 划选 → 标签浮条；点「抽象设定/前后矛盾」**一键直接挂上**（不弹意见卡）；
 *   点「自定义」弹意见卡（免标签名，意见即指令）。
 * - 点击已挂标注的高亮 → 详情卡（标签/原文/意见/状态）→ 可撤回删除。
 * - 高亮走 CSS Custom Highlight，三态着色（DEC-009）；划词/写意见期间置
 *   interacting，usePanelPolling 据此暂停产物刷新（FR-001）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  ANNOTATION_MIN_QUOTE,
  LABEL_OPTIONS,
  applyHighlights,
  captureSelection,
  clearHighlights,
  labelDisplay,
  locateAnnotation,
  buildDomTextIndex,
  type AnnotationLabel,
  type SelectionCapture,
} from "@/lib/annotations";
import { useAnnotationsStore } from "@/stores/annotations";
import type { ArtifactAnnotation } from "@/lib/types";
import { useWorkspaceStore } from "@/stores/workspace";

type FloatPos = { x: number; y: number };

type NoteDraft = {
  capture: SelectionCapture;
  pos: FloatPos;
};

type Detail = {
  ann: ArtifactAnnotation;
  pos: FloatPos;
};

type AnnotationHostProps = {
  /** 当前产物 rel（标注挂在哪个文件上） */
  rel: string;
  /** 是否启用（five 制且范围允许的产物；legacy/主题不启用） */
  enabled: boolean;
  /** 渲染内容（变化后重建高亮） */
  markdown: string;
  /** 重框选目标标注 id（清单抽屉发起）；下一次划选即改锚点 */
  reanchorTarget: string | null;
  onReanchorDone: () => void;
  children: React.ReactNode;
};

const STATUS_TEXT: Record<string, string> = {
  pending: "待处理",
  resolved: "已解决",
  invalid: "已失效",
};

export function AnnotationHost({
  rel,
  enabled,
  markdown,
  reanchorTarget,
  onReanchorDone,
  children,
}: AnnotationHostProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [bar, setBar] = useState<{ pos: FloatPos; capture: SelectionCapture } | null>(null);
  const [draft, setDraft] = useState<NoteDraft | null>(null);
  const [note, setNote] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const annotations = useAnnotationsStore((s) => s.annotations);
  const relAnnotations = useMemo(
    () => annotations.filter((a) => a.artifact_rel === rel),
    [annotations, rel],
  );

  // 高亮：内容或标注变化后重挂（ReactMarkdown 已渲染进容器）
  useEffect(() => {
    const el = containerRef.current;
    if (!el || !enabled) return;
    applyHighlights(el, relAnnotations);
    return () => clearHighlights();
  }, [enabled, relAnnotations, markdown]);

  // 详情卡随标注更新（删除后自动消失）
  useEffect(() => {
    if (detail && !relAnnotations.some((a) => a.id === detail.ann.id)) setDetail(null);
  }, [relAnnotations, detail]);

  const closeOverlays = useCallback((keepSelection: boolean) => {
    setBar(null);
    setDraft(null);
    setNote("");
    setDetail(null);
    useAnnotationsStore.getState().setInteracting(false);
    if (!keepSelection) window.getSelection()?.removeAllRanges();
  }, []);

  // 重框选模式启动时清掉残留浮层（下一次划选直接改锚点）
  useEffect(() => {
    if (reanchorTarget) closeOverlays(true);
  }, [reanchorTarget, closeOverlays]);

  const createNow = useCallback(
    async (label: AnnotationLabel, capture: SelectionCapture, noteText?: string) => {
      const workspaceId = useWorkspaceStore.getState().activeWorkspaceId;
      if (!workspaceId) return;
      try {
        await useAnnotationsStore.getState().create({
          artifact_rel: rel,
          label,
          note: noteText?.trim() || undefined,
          quote: capture.quote,
          context_before: capture.contextBefore,
          context_after: capture.contextAfter,
        });
        toast.success("标注已挂上（待处理）");
        closeOverlays(false);
      } catch (error) {
        // 服务端 422 的 detail（找不到原文/多次出现）直接给用户
        toast.error(error instanceof Error ? error.message : "标注失败，请重试");
      }
    },
    [rel, closeOverlays],
  );

  /** 点击已挂标注的高亮 → 详情卡（caretRangeFromPoint 定位 + isPointInRange 命中）。 */
  const handlePossibleHighlightClick = useCallback(
    (e: React.MouseEvent) => {
      const el = containerRef.current;
      if (!el || relAnnotations.length === 0) return;
      const caretFromPoint = document.caretRangeFromPoint;
      if (typeof caretFromPoint !== "function") return;
      const caret = caretFromPoint(e.clientX, e.clientY);
      if (!caret || !el.contains(caret.startContainer)) return;
      const idx = buildDomTextIndex(el);
      for (const ann of relAnnotations) {
        const range = locateAnnotation(idx, ann);
        if (range?.isPointInRange(caret.startContainer, caret.startOffset)) {
          const hostRect = el.getBoundingClientRect();
          setDetail({
            ann,
            pos: {
              x: Math.max(0, e.clientX - hostRect.left),
              y: Math.max(0, e.clientY - hostRect.top - 6),
            },
          });
          useAnnotationsStore.getState().setInteracting(true);
          return;
        }
      }
    },
    [relAnnotations],
  );

  const handleMouseUp = useCallback(
    (e: React.MouseEvent) => {
      if (!enabled) return;
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed) {
        handlePossibleHighlightClick(e);
        return;
      }
      const el = containerRef.current;
      if (!el) return;
      // 延迟一拍：双击全选、拖选松手后 selection 才稳定
      window.setTimeout(() => {
        const capture = captureSelection(el);
        if (!capture) return;
        const trimmedLen = capture.quote.replace(/\s+/g, "").length;
        if (trimmedLen < ANNOTATION_MIN_QUOTE) {
          // FR-001 失败语义：过短不足以唯一定位 → 提示扩选，不出浮条
          if (capture.quote.trim()) {
            toast.info(`划选太短（至少 ${ANNOTATION_MIN_QUOTE} 个字），多带几个字便于定位`);
          }
          return;
        }
        if (reanchorTarget) {
          void (async () => {
            try {
              await useAnnotationsStore.getState().update(reanchorTarget, {
                reanchor: {
                  quote: capture.quote,
                  context_before: capture.contextBefore,
                  context_after: capture.contextAfter,
                },
              });
              toast.success("已重新框选，标注回到待处理");
              window.getSelection()?.removeAllRanges();
              onReanchorDone();
            } catch (error) {
              toast.error(error instanceof Error ? error.message : "重新框选失败");
            }
          })();
          return;
        }
        let pos: FloatPos = { x: 0, y: 0 };
        try {
          const rect = sel.getRangeAt(0).getBoundingClientRect();
          const hostRect = el.getBoundingClientRect();
          pos = {
            x: Math.max(0, rect.left - hostRect.left + rect.width / 2),
            y: Math.max(0, rect.top - hostRect.top - 6),
          };
        } catch {
          pos = { x: 0, y: 0 };
        }
        setBar({ pos, capture });
        useAnnotationsStore.getState().setInteracting(true);
      }, 10);
    },
    [enabled, reanchorTarget, onReanchorDone, handlePossibleHighlightClick],
  );

  /** 浮条点标签：内置一键挂上（修订 2026-10-27）；自定义转意见卡。 */
  const pickLabel = (label: AnnotationLabel) => {
    if (!bar) return;
    if (label === "custom") {
      setDraft({ capture: bar.capture, pos: bar.pos });
      setBar(null);
      return;
    }
    void createNow(label, bar.capture);
  };

  const confirmCustomNote = async () => {
    if (!draft) return;
    if (!note.trim()) {
      toast.info("自定义标注要写一句意见");
      return;
    }
    await createNow("custom", draft.capture, note);
  };

  const removeDetail = async () => {
    if (!detail) return;
    try {
      await useAnnotationsStore.getState().remove(detail.ann.id);
      toast.success("已撤回该标注");
      closeOverlays(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除失败");
    }
  };

  return (
    <div
      ref={containerRef}
      className={`ann-host${enabled ? " ann-enabled" : ""}`}
      onMouseUp={handleMouseUp}
    >
      {children}
      {enabled && bar && !draft && !detail ? (
        <div className="ann-float-bar" style={{ left: bar.pos.x, top: bar.pos.y }} role="toolbar" aria-label="标注标签">
          {LABEL_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              className="ann-label-btn"
              title={o.value === "custom" ? "写一句意见挂标注" : "点击直接挂上"}
              onClick={() => pickLabel(o.value)}
            >
              {o.display}
            </button>
          ))}
          <button type="button" className="ann-close-btn" aria-label="取消" onClick={() => closeOverlays(false)}>
            ×
          </button>
        </div>
      ) : null}
      {enabled && draft ? (
        <div className="ann-note-card" style={{ left: draft.pos.x, top: draft.pos.y }} role="dialog" aria-label="自定义标注意见">
          <div className="ann-note-quote">{draft.capture.quote.replace(/\s+/g, " ").slice(0, 60)}</div>
          <textarea
            className="ann-note-input"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="意见（必填）：哪里不对、怎么改"
            rows={3}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Escape") closeOverlays(false);
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void confirmCustomNote();
            }}
          />
          <div className="ann-note-actions">
            <Button type="button" variant="outline" onClick={() => closeOverlays(false)}>
              取消
            </Button>
            <Button type="button" onClick={() => void confirmCustomNote()}>
              挂上标注
            </Button>
          </div>
        </div>
      ) : null}
      {enabled && detail ? (
        <div className="ann-note-card ann-detail-card" style={{ left: detail.pos.x, top: detail.pos.y }} role="dialog" aria-label="标注详情">
          <div className="ann-item-top">
            <span className={`ann-chip ann-chip-${detail.ann.status}`}>{STATUS_TEXT[detail.ann.status as string] ?? detail.ann.status}</span>
            <span className="ann-item-label">{labelDisplay(detail.ann)}</span>
          </div>
          <p className="ann-detail-quote">{detail.ann.quote.replace(/\s+/g, " ").slice(0, 120)}</p>
          {detail.ann.note ? <p className="ann-item-note">意见：{detail.ann.note}</p> : null}
          <div className="ann-note-actions">
            <Button type="button" variant="outline" onClick={() => closeOverlays(false)}>
              关闭
            </Button>
            <Button type="button" variant="outline" className="ann-danger-btn" onClick={() => void removeDetail()}>
              撤回删除
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
