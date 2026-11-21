import { useEffect, useLayoutEffect, useState } from "react";
import { Navigate, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Activity,
  BrainCircuit,
  ChartNoAxesCombined,
  Database,
  Dna,
  FileArchive,
  FlaskConical,
  Gauge,
  LogOut,
  PenTool,
  ScrollText,
  ShieldCheck,
  Wrench,
  type LucideIcon } from "lucide-react";
import { fetchMeOrNull, logout, type AuthMe } from "@/lib/api";
import { agentArea, EVOLUTION_AGENT_ASSETS_PATH, isEvolutionAgentAssetsView, pageAgent, rememberPageAgent, withAgent } from "@/lib/writing-agent";
import ChangePasswordDialog from "@/components/ChangePasswordDialog";
import { PageAgentContext } from "@/components/PageAgentSelector";
import { UiFontSizeControl, applyUiFontSize, loadUiFontSize } from "./ui/UiFontSizeControl";

/**
 * Evolution 桌面端主布局（scope 分家 + 管理后台收敛，2026-07-18）。
 *
 * 侧栏导航 + 内容区。登录守卫：未登录跳 /login。
 * 信息架构：
 *   - 基础 8 项（所有用户可见）
 *   - 超管专属 3 项：进化端模型 / 执行端模型 / 管理后台（仅 super_admin）
 */
type NavItem = { to: string; label: string; end: boolean; icon: LucideIcon };
type NavGroup = { title: string; items: NavItem[] };

// 导航分组（评测板块独立，REQ-20260921-135543 FR-001/DEC-005）：
// 观测 / 评测 / 质量闭环 / 系统资产 / 管理。
// 旧 /benchmark、/dataset 入口由 App.tsx 重定向到评测板块。
const NAV_GROUPS: NavGroup[] = [
  {
    title: "观测",
    items: [
      { to: "/", label: "用户运行", end: true, icon: Activity },
      { to: "/analysis", label: "用户分析", end: false, icon: ChartNoAxesCombined },
    ],
  },
  {
    title: "评测",
    items: [
      { to: "/bench", label: "评测工作台", end: true, icon: Gauge },
      { to: "/bench/rules", label: "评测规则", end: false, icon: ScrollText },
      { to: "/bench/dataset", label: "评测数据集", end: false, icon: Database },
      { to: "/bench/experiments", label: "实验记录", end: false, icon: FileArchive },
      { to: EVOLUTION_AGENT_ASSETS_PATH, label: "进化 Agent 资产", end: true, icon: BrainCircuit },
    ],
  },
  {
    title: "质量闭环",
    items: [
      { to: "/evolve", label: "进化", end: false, icon: Dna },
    ],
  },
  {
    title: "系统资产",
    items: [
      { to: "/harness", label: "Harness 资产", end: false, icon: Wrench },
      { to: "/versions", label: "版本谱系", end: false, icon: FileArchive },
    ],
  },
];

// 超管专属导航（仅 super_admin 可见）。单独成组。
const SUPER_ADMIN_NAV_ITEMS: NavItem[] = [
  { to: "/config/evolution", label: "进化端模型", end: false, icon: BrainCircuit },
  { to: "/config/executor", label: "执行端模型", end: false, icon: PenTool },
  { to: "/admin", label: "管理后台", end: false, icon: ShieldCheck },
];

export default function Shell() {
  const navigate = useNavigate();
  const location = useLocation();
  const returnTo = location.pathname + location.search + location.hash;
  const area = agentArea(location.pathname);
  const agent = area && pageAgent(area, location.search);
  useLayoutEffect(() => {
    if (area && agent) rememberPageAgent(area, agent);
  }, [area, agent]);
  const [me, setMe] = useState<AuthMe | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let cancelled = false;
    // 守卫重试：fetchMeOrNull 失败后重试 2 次（间隔 1s/2s），
    // 全部失败才跳登录。避免 executor 瞬时抖动 → 误跳登录页闪烁。
    async function checkAuth() {
      for (let attempt = 0; attempt < 3; attempt++) {
        const user = await fetchMeOrNull();
        if (cancelled) return;
        if (user) {
          setMe(user);
          setChecking(false);
          return;
        }
        if (attempt < 2) await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
        if (cancelled) return;
      }
      // 全部失败：dev 模式用 mock 绕过，生产跳登录
      if (import.meta.env.DEV) {
        setMe({ user_id: "dev-local", username: "本地开发", is_admin: true, is_super_admin: true, has_api_key: false });
        setChecking(false);
        return;
      }
      navigate("/login", { replace: true, state: { returnTo } });
    }
    checkAuth();
    return () => { cancelled = true; };
  }, [navigate, returnTo]);

  // ── 界面字号(DESIGN-UI.md):启动恢复上次选择 ──
  useEffect(() => { applyUiFontSize(loadUiFontSize()); }, []);

  if (checking || !me) {
    return <div className="shell-loading">加载中…</div>;
  }

  // Keep a detail URL bound to its original suite when reopening it from history.
  const query = new URLSearchParams(location.search);
  const showsEvolutionAssets = isEvolutionAgentAssetsView(query);
  const privateDetail = /^\/(traces\/|bench\/experiments\/|evolve\/)/.test(location.pathname)
    || query.has("candidate") || query.has("session");
  if (agent && privateDetail && query.get("agent") !== agent) {
    return <Navigate replace to={withAgent(location.pathname + location.search, agent) + location.hash} />;
  }

  // 构建分组导航（FR-009）：基础三组 + 超管组（仅超管可见）。
  const groups = me.is_super_admin
    ? [...NAV_GROUPS, { title: "管理", items: SUPER_ADMIN_NAV_ITEMS }]
    : NAV_GROUPS;

  async function handleLogout() {
    try {
      await logout();
    } catch {
      // 忽略——本地 cookie 已清，直接跳登录
    }
    navigate("/login", { replace: true });
  }

  return (
    <div className="shell">
      <aside className="shell-sidebar">
        <div className="shell-brand">
          <Dna className="shell-brand-icon" size={19} />
          <span className="shell-brand-text">思衍进化</span>
        </div>
        <nav className="shell-nav">
          {groups.map((group) => (
            <div key={group.title} className="shell-nav-group">
              <div className="shell-nav-group-title">{group.title}</div>
              {group.items.map((item) => {
                const Icon = item.icon;
                const matchesView = (item.to !== "/bench" || !showsEvolutionAssets)
                  && (item.to !== EVOLUTION_AGENT_ASSETS_PATH || showsEvolutionAssets);
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    aria-current={matchesView ? "page" : false}
                    className={({ isActive }) =>
                      `shell-nav-item ${isActive && matchesView ? "active" : ""}`
                    }
                  >
                    <Icon className="shell-nav-icon" size={16} />
                    <span>{item.label}</span>
                  </NavLink>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="shell-sidebar-footer">
          <UiFontSizeControl />
        </div>
        <div className="shell-user">
          <div className="shell-user-info">
            <span className="shell-user-name">{me.username}</span>
            <span className="shell-user-id">{me.user_id.slice(0, 8)}</span>
            <ChangePasswordDialog />
          </div>
          <button className="shell-logout" onClick={handleLogout} title="退出登录" aria-label="退出登录">
            <LogOut size={14} />
          </button>
        </div>
      </aside>
      <main className="shell-main">
        <PageAgentContext.Provider value={{ area, agent, pathname: location.pathname, returnTo }}>
          <Outlet key={agent} context={{ isSuperAdmin: me.is_super_admin }} />
        </PageAgentContext.Provider>
      </main>
    </div>
  );
}
