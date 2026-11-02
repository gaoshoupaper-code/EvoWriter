import { useEffect, useRef, useState, type RefObject } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Pencil, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { importWorkspaceWritingStyle } from "@/lib/api";
import { useWritingStylesStore } from "@/stores/writingStyles";

type Props = {
  mode: "library" | "import" | "save";
  workspaceId?: string;
  onClose: () => void;
  onImported?: (content: string) => void;
  returnFocusRef?: RefObject<HTMLButtonElement | null>;
};

export function WritingStylesDialog({ mode, workspaceId, onClose, onImported, returnFocusRef }: Props) {
  const { assets, loaded, loading, error: loadError } = useWritingStylesStore();
  const [selectedId, setSelectedId] = useState("");
  const [targetId, setTargetId] = useState("");
  const [name, setName] = useState("");
  const [renamingId, setRenamingId] = useState("");
  const [renameName, setRenameName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const mounted = useRef(true);
  const selected = assets.find((asset) => asset.style_id === selectedId);
  const title = mode === "library" ? "个人文风库" : mode === "save" ? "保存文风" : "导入文风";

  useEffect(() => {
    mounted.current = true;
    void useWritingStylesStore.getState().load().catch(() => {});
    return () => { mounted.current = false; };
  }, []);

  async function operate(action: () => Promise<void>) {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError("");
    try { await action(); }
    catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : "操作失败，请重试。"); }
    finally { pending.current = false; if (mounted.current) setBusy(false); }
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!workspaceId || !loaded || loading || loadError) return;
    if (mode === "save") {
      const target = assets.find((asset) => asset.style_id === targetId);
      const saveName = target?.name ?? name.trim();
      if (!saveName || (targetId && !target)) return setError("请填写名称或重新选择要更新的文风。");
      void operate(async () => {
        const saved = await useWritingStylesStore.getState().save(workspaceId, saveName, targetId || undefined);
        if (!saved || !mounted.current) return;
        toast.success("文风已保存到个人库。");
        onClose();
      });
    } else if (mode === "import" && selected) {
      void operate(async () => {
        const result = await importWorkspaceWritingStyle(workspaceId, selected.style_id);
        if (!mounted.current) return;
        onImported?.(result.content);
        toast.success("文风已导入，后续生成使用新文风。");
        onClose();
      });
    }
  }

  return <Dialog.Root open onOpenChange={(open) => { if (!open && !pending.current) onClose(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="modal-overlay">
        <Dialog.Content className="modal-content writing-skills-dialog writing-styles-dialog"
          onEscapeKeyDown={(event) => { if (pending.current) event.preventDefault(); }}
          onInteractOutside={(event) => { if (pending.current) event.preventDefault(); }}
          onCloseAutoFocus={(event) => { if (returnFocusRef) { event.preventDefault(); returnFocusRef.current?.focus(); } }}>
          <header className="skill-dialog-head">
            <Dialog.Title className="modal-title">{title}</Dialog.Title>
            <button type="button" className="skill-dialog-close" aria-label="关闭文风" disabled={busy} onClick={onClose}><X size={16} /></button>
          </header>
          <Dialog.Description className="writing-skills-hint">
            {mode === "library" ? "文风按账号保存。修改或删除库中文风，不影响作品副本。" : mode === "save" ? "整张文风卡原样保存。默认另存，选中旧文风才更新。" : "选择个人文风，直接替换当前作品文风。已有文稿和阶段保留。"}
          </Dialog.Description>
          {loadError ? <div className="writing-skills-error"><p role="alert">{loadError}</p><button type="button" className="sidebar-skill-new" disabled={busy || loading} onClick={() => { void useWritingStylesStore.getState().load().catch(() => {}); }}>重试加载文风</button></div> : null}
          {error ? <p className="error-copy" role="alert">{error}</p> : null}
          {loading ? <p role="status">正在加载文风…</p> : !loadError && loaded ? <>
            {assets.length ? <div className="writing-styles-browser">
              <ul className="writing-styles-list">{assets.map((asset) => <li key={asset.style_id} className={asset.style_id === selectedId ? "selected" : ""}>
                <button type="button" className="writing-style-name" aria-label={`查看 ${asset.name}`} disabled={busy} onClick={() => setSelectedId(asset.style_id)}>{asset.name}</button>
                {mode === "library" ? <span className="sidebar-skill-entry-actions">
                  <button type="button" className="sidebar-skill-entry-btn" aria-label={`改名 ${asset.name}`} disabled={busy} onClick={() => { setRenamingId(asset.style_id); setRenameName(asset.name); setError(""); }}><Pencil size={13} /></button>
                  <button type="button" className="sidebar-skill-entry-btn danger" aria-label={`删除 ${asset.name}`} disabled={busy} onClick={() => {
                    if (!confirm(`删除文风「${asset.name}」？已经导入的作品不受影响。`)) return;
                    void operate(async () => { await useWritingStylesStore.getState().remove(asset.style_id); });
                  }}><Trash2 size={13} /></button>
                </span> : null}
              </li>)}</ul>
              <div className="writing-style-preview markdown-body" aria-label="文风全文">
                {selected ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{selected.content}</ReactMarkdown> : <p className="writing-skills-hint">选择一份文风查看全文。</p>}
              </div>
            </div> : <p className="writing-skills-empty">还没有保存文风。先在作品中通过对话生成文风，再点击「保存文风」。</p>}
            {mode === "library" && renamingId ? <form className="writing-style-rename" onSubmit={(event) => {
              event.preventDefault();
              if (!renameName.trim()) return setError("文风名称不能为空白。");
              void operate(async () => { if (await useWritingStylesStore.getState().rename(renamingId, renameName.trim()) && mounted.current) setRenamingId(""); });
            }}>
              <label className="auth-field"><span>新名称</span><input className="auth-input" value={renameName} disabled={busy} onChange={(event) => setRenameName(event.target.value)} /></label>
              <button type="submit" className="modal-button modal-primary" disabled={busy}>确认改名</button>
              <button type="button" className="modal-button modal-cancel" disabled={busy} onClick={() => setRenamingId("")}>取消</button>
            </form> : null}
          </> : null}
          {mode !== "library" ? <form className="writing-style-save" onSubmit={submit}>
            {mode === "save" ? <>
              <label className="auth-field"><span>保存方式</span><select className="auth-input" value={targetId} disabled={busy || loading || !!loadError} onChange={(event) => setTargetId(event.target.value)}>
                <option value="">另存为新文风</option>{assets.map((asset) => <option value={asset.style_id} key={asset.style_id}>更新「{asset.name}」</option>)}
              </select></label>
              {!targetId ? <label className="auth-field"><span>文风名称</span><input className="auth-input" value={name} disabled={busy} onChange={(event) => setName(event.target.value)} /></label> : null}
            </> : null}
            <div className="modal-actions"><button type="button" className="modal-button modal-cancel" disabled={busy} onClick={onClose}>取消</button>
              <button type="submit" className="modal-button modal-primary" disabled={busy || loading || !loaded || !!loadError || !workspaceId || (mode === "import" && !selected)}>
                {busy ? "处理中…" : mode === "import" ? "导入选中文风" : targetId ? "更新选中文风" : "保存到个人库"}
              </button>
            </div>
          </form> : null}
        </Dialog.Content>
      </Dialog.Overlay>
    </Dialog.Portal>
  </Dialog.Root>;
}
