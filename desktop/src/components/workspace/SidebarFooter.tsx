import { Link } from "react-router-dom";
import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu";
import { Check, ChevronRight, ChevronsUpDown, LogOut, Moon, Settings, Sun } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";

type SidebarFooterProps = {
  theme: "light" | "dark";
  username: string;
  hasApiKey: boolean;
  onThemeToggle: () => void;
  onLogout: () => void;
};

export function SidebarFooter({ theme, username, hasApiKey, onThemeToggle, onLogout }: SidebarFooterProps) {
  return (
    <div className="sidebar-footer">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button className="sidebar-account-trigger" type="button" aria-label="用户菜单" title={username || "用户"}>
            <span className="user-menu-avatar" aria-hidden="true">{(username || "?").slice(0, 1).toUpperCase()}</span>
            <span className="user-menu-name">{username || "用户"}</span>
            <ChevronsUpDown size={14} aria-hidden="true" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="top" align="start" className="sidebar-account-menu w-[220px]">
          <div className="user-menu-header">
            <strong>{username || "用户"}</strong>
            <span className={`user-menu-keybadge ${hasApiKey ? "ok" : "missing"}`}>
              {hasApiKey ? "Key 已配置" : "Key 未配置"}
            </span>
          </div>
          <DropdownMenuSeparator />
          <DropdownMenuPrimitive.Sub>
            <DropdownMenuPrimitive.SubTrigger className="sidebar-theme-trigger relative flex cursor-pointer select-none items-center gap-2 rounded-lg px-2.5 py-2 outline-none focus:bg-accent focus:text-accent-foreground data-[state=open]:bg-accent">
              <Sun size={16} aria-hidden="true" />
              <span>主题</span>
              <ChevronRight size={14} className="ml-auto" aria-hidden="true" />
            </DropdownMenuPrimitive.SubTrigger>
            <DropdownMenuPrimitive.Portal>
              <DropdownMenuPrimitive.SubContent className="sidebar-theme-menu z-50 min-w-[160px] overflow-hidden rounded-xl border border-border bg-popover p-1.5" sideOffset={6}>
                <DropdownMenuPrimitive.RadioGroup
                  value={theme}
                  onValueChange={(value) => {
                    if (value !== theme) onThemeToggle();
                  }}
                >
                  {([{ value: "light", label: "浅色", Icon: Sun }, { value: "dark", label: "深色", Icon: Moon }] as const).map(({ value, label, Icon }) => (
                    <DropdownMenuPrimitive.RadioItem
                      key={value}
                      value={value}
                      className="sidebar-theme-option relative flex cursor-pointer select-none items-center gap-2 rounded-lg px-2.5 py-2 outline-none focus:bg-accent focus:text-accent-foreground"
                    >
                      <Icon size={16} aria-hidden="true" />
                      <span>{label}</span>
                      <DropdownMenuPrimitive.ItemIndicator className="ml-auto">
                        <Check size={16} aria-hidden="true" />
                      </DropdownMenuPrimitive.ItemIndicator>
                    </DropdownMenuPrimitive.RadioItem>
                  ))}
                </DropdownMenuPrimitive.RadioGroup>
              </DropdownMenuPrimitive.SubContent>
            </DropdownMenuPrimitive.Portal>
          </DropdownMenuPrimitive.Sub>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={onLogout}>
            <LogOut size={16} aria-hidden="true" />
            退出登录
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Link className="sidebar-settings-link" to="/settings" aria-label="设置（API Key）" title="设置（API Key）">
        <Settings size={18} aria-hidden="true" />
      </Link>
    </div>
  );
}
