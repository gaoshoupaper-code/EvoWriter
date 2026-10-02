import type { Components } from "react-markdown";

// 表格外包滚动容器（REQ-20261002-125632 FR-003）：
// table 本体按内容取自然宽度（中文窄列不逐字竖排），超宽时由
// wrapper 横向滚动，页面布局不被撑爆。四个 markdown 渲染面板共用。
export const markdownTableComponents: Components = {
  table: ({ children }) => (
    <div className="md-table-wrap">
      <table>{children}</table>
    </div>
  ),
};
