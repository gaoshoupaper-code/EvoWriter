/**
 * AnnotationDrawer —— 标注清单抽屉（REQ-20261027-213246 FR-002/004 UI）。
 *
 * 跨产物聚合全部标注（DEC-010）：状态分色、逐条删除/重新框选（已失效）、
 * 一键清空已解决/已失效；底部「把标注交给陪练」跨产物打包发送（FR-004，
 * 排队语义见 DEC-011——发送动作由父级 ArtifactPanel 走 executionStore）。
 */
import { useMemo } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { labelDisplay } from "@/lib/annotations";
import { useAnnotationsStore } from "@/stores/annotations";

const STATUS_TEXT: Record<string, string> = {
  pending: "待处理",
  resolved: "已解决",
  invalid: "已失效",
};

type AnnotationDrawerProps = {
  open: boolean;
  dispatching: boolean;
  onClose: () => void;
  onDispatch: () => void;
  onReanchor: (annotationId: string) => void;
};

export function AnnotationDrawer({
  open,
  dispatching,
  onClose,
  onDispatch,
  onReanchor,
}: AnnotationDrawerProps) {
  const annotations = useAnnotationsStore((s) => s.annotations);
  const sorted = useMemo(
    () => [...annotations].sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? "")),
    [annotations],
  );
  const pendingCount = sorted.filter((a) => a.status === "pending").length;
  const hasResolved = sorted.some((a) => a.status === "resolved");
  const hasInvalid = sorted.some((a) => a.status === "invalid");

  if (!open) return null;

  const clearStatuses = async (statuses: string[]) => {
    try {
      const deleted = await useAnnotationsStore.getState().clear(statuses);
      toast.success(`已清空 ${deleted} 条`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "清空失败");
    }
  };

  const removeOne = async (annotationId: string) => {
    try {
      await useAnnotationsStore.getState().remove(annotationId);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除失败");
    }
  };

  return (
    <div className="ann-drawer-overlay" role="presentation" onClick={onClose}>
      <aside
        className="ann-drawer"
        role="dialog"
        aria-label="标注清单"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="ann-drawer-head">
          <h3>标注清单</h3>
          <span className="ann-drawer-count">{pendingCount} 条待处理</span>
          <button type="button" className="ann-close-btn" aria-label="关闭" onClick={onClose}>
            ×
          </button>
        </header>

        <div className="ann-drawer-body">
          {sorted.length === 0 ? (
            <p className="ann-drawer-empty">
              还没有标注。在产物里划选一段文字，点标签即可挂上。
            </p>
          ) : (
            sorted.map((ann) => (
              <div key={ann.id} className={`ann-item ann-item-${ann.status}`}>
                <div className="ann-item-top">
                  <span className={`ann-chip ann-chip-${ann.status}`}>
                    {STATUS_TEXT[ann.status as string] ?? ann.status}
                  </span>
                  <span className="ann-item-label">{labelDisplay(ann)}</span>
                  <span className="ann-item-rel">{ann.artifact_rel}</span>
                </div>
                <p className="ann-item-quote">{ann.quote.replace(/\s+/g, " ").slice(0, 80)}</p>
                {ann.note ? <p className="ann-item-note">意见：{ann.note}</p> : null}
                <div className="ann-item-actions">
                  {ann.status === "invalid" ? (
                    <button type="button" onClick={() => onReanchor(ann.id)}>
                      重新框选
                    </button>
                  ) : null}
                  <button type="button" className="ann-danger" onClick={() => void removeOne(ann.id)}>
                    删除
                  </button>
                </div>
              </div>
            ))
          )}
        </div>

        <footer className="ann-drawer-foot">
          <div className="ann-clear-row">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!hasResolved}
              onClick={() => void clearStatuses(["resolved"])}
            >
              清空已解决
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!hasInvalid}
              onClick={() => void clearStatuses(["invalid"])}
            >
              清空已失效
            </Button>
          </div>
          <Button
            type="button"
            disabled={dispatching || pendingCount === 0}
            title={pendingCount === 0 ? "没有待处理的标注" : "把全部待处理标注打包交给陪练"}
            onClick={onDispatch}
          >
            {dispatching ? "发送中…" : `把标注交给陪练（${pendingCount}）`}
          </Button>
        </footer>
      </aside>
    </div>
  );
}
