/**
 * 作品侧栏（REQ-20261018-115508 FR-011，DEC-011）——书选择 + 五段产物树。
 *
 * 上半：书列表（选择/切换/新建/删除）——「一本书一个文件夹」的书架；
 * 超过 5 本默认折叠（REQ-20261025-210512 FR-002），选中的书始终可见。
 * 下半：当前书五段产物树（主题/总纲/文风 + 卷纲逐卷 + 章纲逐章 + 正文逐章），
 * 树顶是可折叠「技能」节点（REQ-20261025-205943，2026-10-26 修订迁入）；
 * 点条目切到产物面板查看；正文条目进入人修编辑（FR-009）。
 * 旧「作品资料」分组（大纲/人物/物品/世界观）随旧机制退役。
 */
import { useMemo, useState, type ReactNode } from "react";
import {
  BookOpen,
  Compass,
  Feather,
  Layers,
  ListTree,
  MessageCircle,
  Plus,
  ScrollText,
  Trash2,
  type LucideIcon,
} from "lucide-react";
import type { OutlineFileEntry, WorkspacePanel, WorkspaceSummary } from "../../lib/types";
import { SidebarSkills } from "./SidebarSkills";

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
  /** 技能分组可见性（REQ-20261025-205943 DEC-011）：仅五段制写作书显示 */
  showSkills?: boolean;
  footer?: ReactNode;
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

/** 书列表折叠阈值（REQ-20261025-210512 FR-002）：超过 5 本默认只露前 5 */
const FOLD_LIMIT = 5;

/** 产物树分组锚点图标（REQ-20261020-200710 DEC-007）：组级小图标，条目保持纯文字 */
const GROUP_ICON: Record<string, LucideIcon> = {
  theme: Compass,
  master: ScrollText,
  style: Feather,
  volume: Layers,
  chapter: ListTree,
  text: BookOpen,
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
  showSkills = true,
  footer,
}: SidebarProps) {
  const groups = treeGroups(files);
  const [booksExpanded, setBooksExpanded] = useState(false);

  /* 可见集（REQ-20261025-210512 FR-002/DEC-002）：默认前 5 本；
     选中的书不在前 5 时追加到末尾——用户始终能看到自己在哪本书上 */
  const shownBooks = useMemo(() => {
    if (booksExpanded || workspaces.length <= FOLD_LIMIT) return workspaces;
    const head = workspaces.slice(0, FOLD_LIMIT);
    const active = workspaces.find((w) => w.workspace_id === activeWorkspaceId);
    return active && !head.some((w) => w.workspace_id === active.workspace_id)
      ? [...head, active]
      : head;
  }, [booksExpanded, workspaces, activeWorkspaceId]);

  return (
    <aside className="dashboard-sidebar" aria-label="作品侧栏">
      <nav className="sidebar-nav">
        {/* ── 创作 ── */}
        <div className="sidebar-group">
          <span className="sidebar-group-label">创作</span>
          <button
            className={`sidebar-item ${activePanel === "chat" ? "active" : ""}`}
            type="button"
            onClick={() => onPanelChange("chat")}
          >
            <span className="sidebar-item-title-row">
              <MessageCircle size={15} aria-hidden="true" />
              <span>对话</span>
            </span>
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
              {creatingWorkspace ? "创建中" : (
                <>
                  <Plus size={12} aria-hidden="true" />
                  新书
                </>
              )}
            </button>
          </div>
          {workspaces.length === 0 ? (
            <p className="sidebar-empty">还没有作品，点「新书」开一本。</p>
          ) : (
            <>
              <ul className="sidebar-books">
                {shownBooks.map((w) => (
                  <li key={w.workspace_id}>
                    <button
                      type="button"
                      className={`sidebar-book${w.workspace_id === activeWorkspaceId ? " active" : ""}`}
                      onClick={() => onSelectWorkspace(w.workspace_id)}
                      title={w.title}
                    >
                      {/* 统一书本图标（REQ-20261025-210512 FR-003/DEC-003）：全部书同款同色 */}
                      <BookOpen size={15} className="sidebar-book-icon" aria-hidden="true" />
                      <span className="sidebar-book-title">{w.title}</span>
                    </button>
                    {/* 删除入口：每行常驻 DOM，悬停/键盘聚焦浮现（FR-003/AC-004），
                        aria-label 语义与既有测试/无障碍约定保持一致 */}
                    <button
                      type="button"
                      className="sidebar-book-delete"
                      onClick={() => onDeleteWorkspace(w.workspace_id)}
                      disabled={deletingWorkspace || creatingWorkspace}
                      aria-label={`删除 ${w.title}`}
                      title={`删除 ${w.title}`}
                    >
                      <Trash2 size={13} aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
              {workspaces.length > FOLD_LIMIT && (
                <button
                  type="button"
                  className="sidebar-books-toggle"
                  aria-expanded={booksExpanded}
                  onClick={() => setBooksExpanded((v) => !v)}
                >
                  {booksExpanded ? "收起" : `展开其余 ${workspaces.length - shownBooks.length} 本`}
                </button>
              )}
            </>
          )}
        </div>

        {/* ── 五段产物树：点条目进产物面板查看 ── */}
        <div className="sidebar-group sidebar-tree">
          <span className="sidebar-group-label">产物</span>
          {/* 技能节点（REQ-20261025-205943；2026-10-26 修订）：迁入产物树、
              主题上方，可折叠——仅五段制写作书显示 */}
          {showSkills ? <SidebarSkills /> : null}
          {groups.length === 0 ? (
            <p className="sidebar-empty">开始对话后，产物会在这里出现。</p>
          ) : (
            groups.map((g) => {
              const Icon = GROUP_ICON[g.id];
              /* 单条目组（REQ-20261025-210512 FR-005）：组头折叠进条目——
                 主题/总纲/文风各只有一份文档，组标签+同名条目是冗余的两层 */
              const solo = g.rels.length === 1;
              return (
              <div className="sidebar-tree-group" key={g.id}>
                {!solo && (
                  <span className="sidebar-tree-group-label">
                    {Icon ? <Icon size={13} aria-hidden="true" /> : null}
                    {g.label}
                  </span>
                )}
                <ul>
                  {g.rels.map((label) => {
                    const rel = relOfLabel(GROUP_DIR[g.id], label);
                    return (
                      <li key={rel}>
                        <button
                          type="button"
                          className={`sidebar-tree-item${solo ? " solo" : ""}${rel === activeRel && activePanel === "artifacts" ? " active" : ""}`}
                          onClick={() => onSelectArtifact(rel)}
                          title={label}
                        >
                          {solo && Icon ? <Icon size={13} className="sidebar-tree-icon" aria-hidden="true" /> : null}
                          {label}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
              );
            })
          )}
        </div>
      </nav>
      {footer}
    </aside>
  );
}
