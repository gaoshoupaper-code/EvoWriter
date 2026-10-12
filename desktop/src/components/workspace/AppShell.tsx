import type { ReactNode } from "react";

type AppShellProps = {
  topBar: ReactNode;
  sidebar: ReactNode;
  /** 右侧常驻作品栏（写作域渲染，其他域传 null——第三列自动收为 0 宽） */
  rail?: ReactNode;
  children: ReactNode;
};

export function AppShell({ topBar, sidebar, rail, children }: AppShellProps) {
  return (
    <main className="dashboard-shell">
      {topBar}
      <div className="dashboard-body">
        {sidebar}
        <section className="dashboard-main">{children}</section>
        {rail}
      </div>
    </main>
  );
}
