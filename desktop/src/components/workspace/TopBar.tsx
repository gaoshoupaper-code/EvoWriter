/** 顶栏只保留品牌；账户、主题与配置入口集中到侧栏底部。 */
export function TopBar() {
  return (
    <header className="dashboard-topbar">
      <div className="brand">
        <img className="brand-mark" src="/favicon-256.png" alt="" />
        <span>
          <strong>思衍</strong>
          <small>AI writing workspace</small>
        </span>
      </div>
    </header>
  );
}
