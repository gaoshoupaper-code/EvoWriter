/**
 * AnnotationHost —— 产物划词标注宿主（REQ-20261027-213246 FR-001/002 UI）。
 *
 * 包在渲染态 markdown 外层：划选 → 标签浮条（抽象设定/前后矛盾/自定义）→
 * 意见小卡（选填）→ 挂上高亮（CSS Custom Highlight）。标注按状态着色：
 * 待处理黄、已解决绿、已失效灰（DEC-009）。划词/写意见期间置 interacting，
 * usePanelPolling 据此暂停产物刷新（FR-001：不打断选区、不丢意见）。
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
  type AnnotationLabel,
  type SelectionCapture,
} from "@/lib/annotations";
import { useAnnotationsStore } from "@/stores/annotations";
import { useWorkspaceStore } from "@/stores/workspace";

type FloatPos = { x: number; y: number };

type Draft = {
  label: AnnotationLabel;
  customLabel: string;
  note: string;
  capture: SelectionCapture;
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
  const [draft, setDraft] = useState<Draft | null>(null);
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

  const closeOverlays = useCallback((keepSelection: boolean) => {
    setBar(null);
    setDraft(null);
    useAnnotationsStore.getState().setInteracting(false);
    if (!keepSelection) window.getSelection()?.removeAllRanges();
  }, []);

  // 重框选模式启动时清掉残留浮层（下一次划选直接改锚点）
  useEffect(() => {
    if (reanchorTarget) closeOverlays(true);
  }, [reanchorTarget, closeOverlays]);

  const handleMouseUp = useCallback(() => {
    if (!enabled) return;
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
      const sel = window.getSelection();
      if (!sel || sel.rangeCount === 0) return;
      // 浮条贴选区上方；矩形不可得（极端环境）时退到容器左上，不阻断标注
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
  }, [enabled, reanchorTarget, onReanchorDone]);

  const openDraft = (label: AnnotationLabel) => {
    if (!bar) return;
    setDraft({ label, customLabel: "", note: "", capture: bar.capture, pos: bar.pos });
    setBar(null);
  };

  const confirmDraft = async () => {
    if (!draft) return;
    if (draft.label === "custom" && !draft.customLabel.trim()) {
      toast.info("自定义标签要先填标签名");
      return;
    }
    const workspaceId = useWorkspaceStore.getState().activeWorkspaceId;
    if (!workspaceId) return;
    try {
      await useAnnotationsStore.getState().create({
        artifact_rel: rel,
        label: draft.label,
        custom_label: draft.label === "custom" ? draft.customLabel.trim() : undefined,
        note: draft.note.trim() || undefined,
        quote: draft.capture.quote,
        context_before: draft.capture.contextBefore,
        context_after: draft.capture.contextAfter,
      });
      toast.success("标注已挂上（待处理）");
      closeOverlays(false);
    } catch (error) {
      // 服务端 422 的 detail（找不到原文/多次出现）直接给用户
      toast.error(error instanceof Error ? error.message : "标注失败，请重试");
    }
  };

  return (
    <div
      ref={containerRef}
      className={`ann-host${enabled ? " ann-enabled" : ""}`}
      onMouseUp={handleMouseUp}
    >
      {children}
      {enabled && bar && !draft ? (
        <div className="ann-float-bar" style={{ left: bar.pos.x, top: bar.pos.y }} role="toolbar" aria-label="标注标签">
          {LABEL_OPTIONS.map((o) => (
            <button key={o.value} type="button" className="ann-label-btn" onClick={() => openDraft(o.value)}>
              {o.display}
            </button>
          ))}
          <button type="button" className="ann-close-btn" aria-label="取消" onClick={() => closeOverlays(false)}>
            ×
          </button>
        </div>
      ) : null}
      {enabled && draft ? (
        <div className="ann-note-card" style={{ left: draft.pos.x, top: draft.pos.y }} role="dialog" aria-label="标注意见">
          <div className="ann-note-quote">{draft.capture.quote.replace(/\s+/g, " ").slice(0, 60)}</div>
          {draft.label === "custom" ? (
            <input
              className="ann-custom-input"
              value={draft.customLabel}
              onChange={(e) => setDraft({ ...draft, customLabel: e.target.value })}
              placeholder="标签名（必填）"
              autoFocus
            />
          ) : null}
          <textarea
            className="ann-note-input"
            value={draft.note}
            onChange={(e) => setDraft({ ...draft, note: e.target.value })}
            placeholder={draft.label === "custom" ? "意见（必填）：哪里不对、怎么改" : "意见（选填）：写了就追加在默认指令后"}
            rows={3}
            autoFocus={draft.label !== "custom"}
            onKeyDown={(e) => {
              if (e.key === "Escape") closeOverlays(false);
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void confirmDraft();
            }}
          />
          <div className="ann-note-actions">
            <Button type="button" variant="outline" onClick={() => closeOverlays(false)}>
              取消
            </Button>
            <Button type="button" onClick={() => void confirmDraft()}>
              挂上标注
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
