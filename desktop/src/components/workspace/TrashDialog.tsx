/**
 * TrashDialog —— 回收站（REQ-20261030-211446 FR-006 / DEC-005）。
 *
 * 列出删除重做移入 .writing/trash 的产物（原名/类型/删除时间，倒序），
 * 一键恢复回原位：原位被新文件占住时服务端 409 不覆盖；恢复不动段
 * 指针、不恢复已清除的标注（进度由「目录即进度」自然重算）。
 */
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { listTrash, restoreTrash, type TrashEntry } from "@/lib/api";
import { stageDisplayLabel } from "@/lib/artifact-redo";
import { refreshWorkspaceContent } from "@/lib/usePanelPolling";

type TrashDialogProps = {
  open: boolean;
  workspaceId: string;
  onClose: () => void;
};

export function TrashDialog({ open, workspaceId, onClose }: TrashDialogProps) {
  const [items, setItems] = useState<TrashEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busyRel, setBusyRel] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    listTrash(workspaceId)
      .then((result) => {
        if (!cancelled) setItems(result.items);
      })
      .catch((error) => {
        // 失败与空态分开：旧 executor（无此端点）会 404，折叠成「空的」会误导
        const detail = error instanceof Error ? error.message : "";
        if (!cancelled) setLoadError(detail ? `读取回收站失败：${detail}` : "读取回收站失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, workspaceId]);

  if (!open) return null;

  const handleRestore = async (entry: TrashEntry) => {
    setBusyRel(entry.trash_rel);
    try {
      const result = await restoreTrash(workspaceId, entry.trash_rel);
      toast.success(`已恢复「${result.restored}」`);
      setItems((prev) => prev.filter((i) => i.trash_rel !== entry.trash_rel));
      await refreshWorkspaceContent(workspaceId);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "恢复失败，请重试");
    } finally {
      setBusyRel(null);
    }
  };

  return (
    <div className="modal-overlay" role="presentation">
      <section className="modal-content trash-modal" role="dialog" aria-modal="true" aria-labelledby="trash-dialog-title">
        <h2 className="modal-title" id="trash-dialog-title">回收站</h2>
        <p className="modal-description">
          删除重做的产物都在这里，点「恢复」放回原位。原位已有新文件时需先删除新文件才能恢复；恢复不会改动当前创作阶段。
        </p>
        {loading ? (
          <p className="trash-empty">读取中…</p>
        ) : loadError ? (
          <p className="trash-empty trash-error">{loadError}</p>
        ) : items.length === 0 ? (
          <p className="trash-empty">回收站是空的</p>
        ) : (
          <ul className="trash-list">
            {items.map((item) => (
              <li key={item.trash_rel} className="trash-item">
                <div className="trash-item-meta">
                  <span className="trash-item-rel">{item.original_rel}</span>
                  <span className="trash-item-sub">{stageDisplayLabel(item.kind_label)} · {item.deleted_at} 删除</span>
                </div>
                <button
                  type="button"
                  className="trash-restore-btn"
                  disabled={busyRel !== null}
                  onClick={() => void handleRestore(item)}
                >
                  {busyRel === item.trash_rel ? "恢复中" : "恢复"}
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="modal-actions">
          <button className="modal-button modal-cancel" type="button" onClick={onClose} disabled={busyRel !== null}>
            关闭
          </button>
        </div>
      </section>
    </div>
  );
}
