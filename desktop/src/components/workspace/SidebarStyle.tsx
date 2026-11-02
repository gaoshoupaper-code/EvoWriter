import { useRef, useState } from "react";
import { Download, Feather, Save } from "lucide-react";
import { WritingStylesDialog } from "./WritingStylesDialog";
import { useContentStore } from "@/stores/content";
import { useWorkspaceStore } from "@/stores/workspace";
import { useAnnotationsStore } from "@/stores/annotations";

type Props = { workspaceId: string; hasStyle: boolean; active: boolean; onOpen: () => void; filesLoading: boolean; filesError: string };

export function SidebarStyle({ workspaceId, hasStyle, active, onOpen, filesLoading, filesError }: Props) {
  const [mode, setMode] = useState<"save" | "import" | null>(null);
  const importTrigger = useRef<HTMLButtonElement>(null);
  const saveTrigger = useRef<HTMLButtonElement>(null);
  if (!workspaceId) return null;

  function imported(content: string) {
    if (useWorkspaceStore.getState().activeWorkspaceId !== workspaceId) return;
    // 用服务端实际写入结果刷新，避免成功后刷新失败被误当作写入失败。
    useContentStore.setState((state) => ({
      outlineWorkspaceId: workspaceId,
      outlineError: "",
      activeOutlineRel: "文风.md",
      outlineFiles: [...state.outlineFiles.filter((file) => file.rel !== "文风.md"), { rel: "文风.md", markdown: content }],
    }));
    useAnnotationsStore.setState((state) => state.workspaceId === workspaceId ? {
      annotations: state.annotations.map((annotation) => annotation.artifact_rel === "文风.md" && annotation.status === "pending" ? { ...annotation, status: "invalid" as const } : annotation),
    } : {});
    useWorkspaceStore.setState({ activePanel: "artifacts" });
  }

  return <div className="sidebar-tree-group sidebar-style" role="group" aria-label="作品文风">
    <ul><li><button type="button" className={`sidebar-tree-item solo${active ? " active" : ""}`} disabled={!hasStyle} onClick={onOpen} title="文风">
      <Feather size={13} className="sidebar-tree-icon" aria-hidden="true" />文风
    </button></li></ul>
    {!hasStyle && !filesLoading && !filesError ? <p className="sidebar-empty">暂无文风，可导入或通过对话生成。</p> : null}
    <div className="sidebar-style-actions">
      <button ref={importTrigger} type="button" className="sidebar-skill-new" onClick={() => setMode("import")}><Download size={12} aria-hidden="true" />导入文风</button>
      <button ref={saveTrigger} type="button" className="sidebar-skill-new" disabled={!hasStyle || filesLoading || !!filesError} onClick={() => setMode("save")}><Save size={12} aria-hidden="true" />保存文风</button>
    </div>
    {mode ? <WritingStylesDialog mode={mode} workspaceId={workspaceId} onClose={() => setMode(null)} onImported={imported} returnFocusRef={mode === "import" ? importTrigger : saveTrigger} /> : null}
  </div>;
}
