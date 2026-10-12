import type { WorkspacePanel } from "../../lib/types";

type SidebarItem = {
  id: WorkspacePanel;
  label: string;
  description: string;
};

type SidebarGroup = {
  title: string;
  items: SidebarItem[];
};

/** 两组五入口（REQ-20261012-202540 FR-006，DEC-005/008）：
 * 「创作」= 对话；「作品资料」= 全屏深读入口。旧「大纲（旧）」入口已删（DEC-007）。 */
const SIDEBAR_GROUPS: SidebarGroup[] = [
  {
    title: "创作",
    items: [
      { id: "chat", label: "对话", description: "和小衍一起推进故事" },
    ],
  },
  {
    title: "作品资料",
    items: [
      { id: "outline", label: "大纲", description: "七步法从赛道到章纲" },
      { id: "characters", label: "人物", description: "角色档案与关系" },
      { id: "objects", label: "物品", description: "关键物品与出现轨迹" },
      { id: "worldview", label: "世界观", description: "背景设定与规则" },
    ],
  },
];

type SidebarProps = {
  activePanel: WorkspacePanel;
  onPanelChange: (panel: WorkspacePanel) => void;
};

export function Sidebar({ activePanel, onPanelChange }: SidebarProps) {
  return (
    <aside className="dashboard-sidebar" aria-label="工作台模块">
      <nav className="sidebar-nav">
        {SIDEBAR_GROUPS.map((group) => (
          <div className="sidebar-group" key={group.title}>
            <span className="sidebar-group-label">{group.title}</span>
            {group.items.map((item) => {
              const isActive = activePanel === item.id;
              return (
                <button
                  className={`sidebar-item ${isActive ? "active" : ""} transition-all duration-150`}
                  type="button"
                  key={item.id}
                  onClick={() => onPanelChange(item.id)}
                >
                  <span>{item.label}</span>
                  <small>{item.description}</small>
                </button>
              );
            })}
          </div>
        ))}
      </nav>
    </aside>
  );
}
