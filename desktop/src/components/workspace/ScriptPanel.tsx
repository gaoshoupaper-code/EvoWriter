import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { PanoramaEvent, StorylineEntry } from "../../lib/types";

type ScriptPanelProps = {
  storylineMarkdown: string;
  storylineEntries: StorylineEntry[];
  storylinePanorama: PanoramaEvent[];
  storylineFormat: string;
  activeStorylineFilename: string;
  onSelectStoryline: (filename: string) => void;
};

// 「大纲全景」导航首项 = 故事核心 + 跨线全景表；其余项为各故事线区块。
// 线区块选中键用 title（线名）——v2 各线 filename 同为 storyline.md，
// 用 filename 会让选中永远命中第一条线（本次修复的现存缺陷）。
const CORE_KEY = "__core__";

type StorylineItem = { key: string; title: string; markdown: string };

/** 故事核心 = storyline.md 中第一个二级标题（线区块/一览表）之前的部分。 */
function extractCoreMarkdown(markdown: string): string {
  const match = markdown.match(/^#.*?(?=^## )/ms);
  return (match ? match[0] : markdown).trim();
}

export function ScriptPanel({
  storylineMarkdown,
  storylineEntries,
  storylinePanorama,
  storylineFormat,
  activeStorylineFilename,
  onSelectStoryline,
}: ScriptPanelProps) {
  const isLegacy = storylineFormat === "legacy";
  const items: StorylineItem[] = [
    ...(storylineMarkdown.trim() ? [{ key: CORE_KEY, title: "故事核心", markdown: extractCoreMarkdown(storylineMarkdown) }] : []),
    ...storylineEntries.map((entry) => ({ key: entry.title, title: entry.title, markdown: entry.markdown })),
  ];
  const activeKey = items.some((i) => i.key === activeStorylineFilename)
    ? activeStorylineFilename
    : (items[0]?.key ?? "");
  const active = items.find((e) => e.key === activeKey);

  // v2 解析失败（panorama 空）→ 降级渲染 storyline.md 原文；legacy → 按线分区块（FR-013 降级）
  const panoramaAvailable = !isLegacy && storylinePanorama.length > 0;
  const fallbackMarkdownView = !isLegacy && !panoramaAvailable && storylineMarkdown.trim().length > 0;

  return (
    <section className="panel-surface content-panel" aria-label="大纲全景">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">Panorama</span>
          <h2>大纲全景</h2>
        </div>
      </div>

      <div className="content-panel-body">
        {fallbackMarkdownView ? (
          <article className="outline-markdown detail-outline-markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{storylineMarkdown}</ReactMarkdown>
          </article>
        ) : items.length > 0 ? (
          <div className="detail-outline-layout">
            <aside className="detail-outline-sidebar" aria-label="故事线导航">
              <span className="field-label">故事线</span>
              <div className="detail-outline-list">
                {items.map((entry) => (
                  <button
                    className={`detail-outline-list-item${entry.key === active?.key ? " active" : ""}`}
                    key={entry.key}
                    type="button"
                    onClick={() => onSelectStoryline(entry.key)}
                  >
                    <span>{entry.title}</span>
                  </button>
                ))}
              </div>
            </aside>

            {active && active.key !== CORE_KEY ? (
              <article className="outline-markdown detail-outline-markdown">
                {active.markdown.trim() ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{active.markdown}</ReactMarkdown>
                ) : (
                  <p>该故事线暂无内容。</p>
                )}
              </article>
            ) : active ? (
              <article className="outline-markdown detail-outline-markdown">
                {active.markdown.trim() ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{active.markdown}</ReactMarkdown>
                ) : null}
                {panoramaAvailable ? (
                  <>
                    <h3>跨线全景</h3>
                    <p className="field-label">全部故事线的事件按剧情时序合并——交汇事件的所属线列出全部参与线。</p>
                    <table>
                      <thead>
                        <tr>
                          <th>时序</th>
                          <th>所属线</th>
                          <th>事件</th>
                          <th>类型</th>
                          <th>角色</th>
                          <th>地点</th>
                          <th>描述</th>
                        </tr>
                      </thead>
                      <tbody>
                        {storylinePanorama.map((ev) => (
                          <tr key={`${ev.t}-${ev.name}`}>
                            <td>{ev.t}</td>
                            <td>{ev.storylines.join("、")}</td>
                            <td>{ev.name}</td>
                            <td>{ev.type}</td>
                            <td>{ev.characters}</td>
                            <td>{ev.location}</td>
                            <td>{ev.desc}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                ) : null}
              </article>
            ) : (
              <div className="empty-state">
                <span className="placeholder-mark">暂无大纲</span>
                <h3>当前工作目录中暂无故事线</h3>
                <p>发起创作后，故事核心与跨线全景表会在这里显示。</p>
              </div>
            )}
          </div>
        ) : (
          <div className="empty-state">
            <span className="placeholder-mark">暂无大纲</span>
            <h3>当前工作目录中暂无故事线</h3>
            <p>发起创作后，故事核心与跨线全景表会在这里显示。</p>
          </div>
        )}
      </div>
    </section>
  );
}
