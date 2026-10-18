import { Link } from "react-router-dom";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";

type ThemeMode = "light" | "dark";

type TopBarProps = {
  theme: ThemeMode;
  username: string;
  isAdmin: boolean;
  hasApiKey: boolean;
  onThemeToggle: () => void;
  onLogout: () => void;
};

/** 顶栏（REQ-20261018-115508 DEC-011）：书选择迁入作品侧栏，
 * 顶栏只留品牌 + 用户菜单（设置/登出）+ 主题切换。 */
export function TopBar({
  theme,
  username,
  hasApiKey,
  onThemeToggle,
  onLogout,
}: TopBarProps) {
  return (
    <header className="dashboard-topbar">
      <div className="brand">
        <img className="brand-mark" src="/favicon-256.png" alt="" />
        <span>
          <strong>思衍</strong>
          <small>AI writing workspace</small>
        </span>
      </div>

      <div className="workspace-actions">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="user-menu-trigger" type="button" aria-label="用户菜单">
              <span className="user-menu-avatar">{(username || "?").slice(0, 1).toUpperCase()}</span>
              <span className="user-menu-name">{username || "用户"}</span>
              <span className="session-menu-caret">▾</span>
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-[220px]">
            <div className="user-menu-header">
              <strong>{username || "用户"}</strong>
              <span className={`user-menu-keybadge ${hasApiKey ? "ok" : "missing"}`}>
                {hasApiKey ? "Key 已配置" : "Key 未配置"}
              </span>
            </div>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link to="/settings">设置（API Key）</Link>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="text-red-600" onClick={onLogout}>
              退出登录
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        <button
          className="theme-toggle"
          type="button"
          role="switch"
          aria-checked={theme === "dark"}
          aria-label="切换亮色和暗色模式"
          onClick={onThemeToggle}
        >
          <span className="theme-toggle-track" aria-hidden="true">
            <span className="theme-toggle-thumb" />
          </span>
          <span>{theme === "dark" ? "暗色" : "亮色"}</span>
        </button>
      </div>
    </header>
  );
}
