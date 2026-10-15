import type { ReactNode } from "react";

type AppShellProps = {
  topBar: ReactNode;
  sidebar: ReactNode;
  /** 作品浮窗（写作域渲染，其他域传 null）：三态均 absolute 悬浮，不占栅格列 */
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
