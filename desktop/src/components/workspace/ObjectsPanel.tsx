import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { markdownTableComponents } from "../../lib/markdown-components";
import type { ObjectMarkdownFile } from "../../lib/types";

type ObjectsPanelProps = {
  objects: ObjectMarkdownFile[];
  activeFilename: string;
  loading: boolean;
  onSelectObject: (filename: string) => void;
};

export function ObjectsPanel({ objects, activeFilename, loading, onSelectObject }: ObjectsPanelProps) {
  const activeObject = objects.find((object) => object.filename === activeFilename) ?? objects[0];
  const contentRef = useRef<HTMLDivElement | null>(null);

  // 切换物品后内容列回顶（FR-003）；依赖 filename 而非 markdown——同条目刷新不打断阅读
  useEffect(() => {
    if (contentRef.current) contentRef.current.scrollTop = 0;
  }, [activeObject?.filename]);

  return (
    <section className="panel-surface content-panel" aria-label="物品状态卡">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">Objects</span>
          <h2>物品状态卡</h2>
        </div>
        {loading ? <span className="outline-state">加载中</span> : null}
      </div>

      <div className="content-panel-body">
        {objects.length ? (
          <div className="character-layout">
            <aside className="character-sidebar" aria-label="物品信息表">
              <span className="field-label">物品信息表</span>
              <div className="character-list">
                {objects.map((object) => (
                  <button
                    className={`character-list-item${object.filename === activeObject?.filename ? " active" : ""}`}
                    key={object.filename}
                    type="button"
                    onClick={() => onSelectObject(object.filename)}
                  >
                    <span>{object.name}</span>
                    <small>{object.filename}</small>
                  </button>
                ))}
              </div>
            </aside>

            <div className="character-content" ref={contentRef}>
              <article className="outline-markdown character-markdown">
                {activeObject?.markdown.trim() ? <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={markdownTableComponents}>{activeObject.markdown}</ReactMarkdown> : <p>这个物品文件暂无内容。</p>}
              </article>
            </div>
          </div>
        ) : (
          <div className="empty-state">
            <span className="placeholder-mark">暂无物品卡</span>
            <h3>本作品暂无物品卡</h3>
            <p>生成大纲后，关键物品（功法、武技、武器、丹药等）的卡片会出现在后端工作目录的 object 文件夹，并在这里显示。</p>
          </div>
        )}
      </div>
    </section>
  );
}
