import { NavLink, Outlet } from "react-router-dom";
import "@/styles/admin-billing.css";

/** Super-admin navigation. Credit statements and public price settings share this boundary. */

const ADMIN_TABS = [
  { to: "/admin/users", label: "用户" },
  { to: "/admin/invite-codes", label: "邀请码" },
  { to: "/admin/credits", label: "积分账目" },
  { to: "/admin/credits/settings", label: "积分设置" },
];

export default function AdminLayout() {
  return (
    <div className="admin-layout">
      <header className="page-header">
        <h1>管理后台</h1>
        <p className="page-desc">
          管理用户、邀请码、积分账目与模型积分系数。所有积分调整都会留痕。
        </p>
      </header>
      <nav className="admin-tabs">
        {ADMIN_TABS.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            // 积分流水(/admin/credits) 和 积分设置(/admin/credits/settings) 是父子路径，
            // 用 end 让"积分流水"只在精确匹配时高亮，避免被 settings 抢走高亮。
            end={tab.to === "/admin/credits"}
            className={({ isActive }) =>
              `admin-tab ${isActive ? "active" : ""}`
            }
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>
      <div className="admin-content">
        <Outlet />
      </div>
    </div>
  );
}
