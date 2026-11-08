import { Link } from "react-router-dom";

export default function RetiredBenchmark() {
  return <div className="bench-page"><header className="page-header"><h1>旧版批次评测已下线</h1>
    <p className="page-desc">实验运行、评分和文稿现在统一保存在实验记录中。</p></header>
    <Link className="config-button primary" to="/bench/experiments">查看实验记录</Link></div>;
}
