/**
 * 作品侧栏（REQ-20261018-115508 FR-011，DEC-011）——书选择 + 五段产物树。
 *
 * 上半：书列表（选择/切换/新建/删除）——「一本书一个文件夹」的书架。
 * 下半：当前书五段产物树（主题/总纲/文风 + 卷纲逐卷 + 章纲逐章 + 正文逐章），
 * 点条目切到产物面板查看；正文条目进入人修编辑（FR-009）。
 * 旧「作品资料」分组（大纲/人物/物品/世界观）随旧机制退役。
 */
import type { OutlineFileEntry, WorkspacePanel, WorkspaceSummary } from "../../lib/types";

type SidebarProps = {
  activePanel: WorkspacePanel;
  onPanelChange: (panel: WorkspacePanel) => void;
  workspaces: WorkspaceSummary[];
  activeWorkspaceId: string;
  creatingWorkspace: boolean;
  deletingWorkspace: boolean;
  onSelectWorkspace: (workspaceId: string) => void;
  onCreateWorkspace: () => void;
  onDeleteWorkspace: (workspaceId: string) => void;
  files: OutlineFileEntry[];
  activeRel: string;
  onSelectArtifact: (rel: string) => void;
};

/** rel → 树分组（顺序即五段流程序；空组不显示） */
function treeGroups(files: OutlineFileEntry[]): { id: string; label: string; rels: string[] }[] {
  const label = (rel: string) => rel.split("/").pop()!.replace(/\.md$/, "");
  const groups: Record<string, string[]> = {
    theme: [], master: [], style: [], volume: [], chapter: [], text: [],
  };
  for (const f of files) {
    if (f.rel === "主题.md") groups.theme.push(label(f.rel));
    else if (f.rel === "总纲.md") groups.master.push(label(f.rel));
    else if (f.rel === "文风.md") groups.style.push(label(f.rel));
    else if (f.rel.startsWith("卷纲/")) groups.volume.push(label(f.rel));
    else if (f.rel.startsWith("章纲/")) groups.chapter.push(label(f.rel));
    else if (f.rel.startsWith("正文/")) groups.text.push(label(f.rel));
  }
  return [
    { id: "theme", label: "主题", rels: groups.theme },
    { id: "master", label: "总纲", rels: groups.master },
    { id: "style", label: "文风", rels: groups.style },
    { id: "volume", label: "卷纲", rels: groups.volume },
    { id: "chapter", label: "章纲", rels: groups.chapter },
    { id: "text", label: "正文", rels: groups.text },
  ].filter((g) => g.rels.length > 0);
}

function relOfLabel(dir: string | null, label: string): string {
  return dir ? `${dir}/${label}.md` : `${label}.md`;
}

const GROUP_DIR: Record<string, string | null> = {
  theme: null, master: null, style: null,
  volume: "卷纲", chapter: "章纲", text: "正文",
};

export function Sidebar({
  activePanel,
  onPanelChange,
  workspaces,
  activeWorkspaceId,
  creatingWorkspace,
  deletingWorkspace,
  onSelectWorkspace,
  onCreateWorkspace,
  onDeleteWorkspace,
  files,
  activeRel,
  onSelectArtifact,
}: SidebarProps) {
  const groups = treeGroups(files);

  return (
    <aside className="dashboard-sidebar" aria-label="作品侧栏">
      <nav className="sidebar-nav">
        {/* ── 创作 ── */}
        <div className="sidebar-group">
          <span className="sidebar-group-label">创作</span>
          <button
            className={`sidebar-item ${activePanel === "chat" ? "active" : ""} transition-all duration-150`}
            type="button"
            onClick={() => onPanelChange("chat")}
          >
            <span>对话</span>
            <small>和陪练一起推进这本书</small>
          </button>
        </div>

        {/* ── 书架：书列表（选择/切换/新建/删除）── */}
        <div className="sidebar-group">
          <div className="sidebar-group-row">
            <span className="sidebar-group-label">作品</span>
            <button
              className="sidebar-new-book"
              type="button"
              onClick={onCreateWorkspace}
              disabled={creatingWorkspace}
              aria-label="新建作品"
              title="新建作品"
            >
              {creatingWorkspace ? "创建中" : "＋ 新书"}
            </button>
          </div>
          {workspaces.length === 0 ? (
            <p className="sidebar-empty">还没有作品，点「新书」开一本。</p>
          ) : (
            <ul className="sidebar-books">
              {workspaces.map((w) => (
                <li key={w.workspace_id}>
                  <button
                    type="button"
                    className={`sidebar-book${w.workspace_id === activeWorkspaceId ? " active" : ""}`}
                    onClick={() => onSelectWorkspace(w.workspace_id)}
                    title={w.title}
                  >
                    <span className="sidebar-book-icon">{w.domain === "image" ? "🎨" : "📕"}</span>
                    <span className="sidebar-book-title">{w.title}</span>
                  </button>
                  {w.workspace_id === activeWorkspaceId ? (
                    <button
                      type="button"
                      className="sidebar-book-delete"
                      onClick={() => onDeleteWorkspace(w.workspace_id)}
                      disabled={deletingWorkspace || creatingWorkspace}
                      aria-label={`删除 ${w.title}`}
                      title={`删除 ${w.title}`}
                    >
                      删除
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* ── 五段产物树：点条目进产物面板查看 ── */}
        <div className="sidebar-group sidebar-tree">
          <span className="sidebar-group-label">产物</span>
          {groups.length === 0 ? (
            <p className="sidebar-empty">开始对话后，产物会在这里出现。</p>
          ) : (
            groups.map((g) => (
              <div className="sidebar-tree-group" key={g.id}>
                <span className="sidebar-tree-group-label">{g.label}</span>
                <ul>
                  {g.rels.map((label) => {
                    const rel = relOfLabel(GROUP_DIR[g.id], label);
                    return (
                      <li key={rel}>
                        <button
                          type="button"
                          className={`sidebar-tree-item${rel === activeRel && activePanel === "artifacts" ? " active" : ""}`}
                          onClick={() => onSelectArtifact(rel)}
                          title={label}
                        >
                          {label}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))
          )}
        </div>
      </nav>
    </aside>
  );
}
