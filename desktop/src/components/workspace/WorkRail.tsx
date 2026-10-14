/**
 * WorkRail —— 右上角作品浮窗（REQ-20261013-181350 FR-001，DEC-002/005/006/009/010/011）。
 *
 * 三态浮窗（不占 dashboard 栅格，悬浮于对话区右上角，完全固定不可拖）：
 * closed   只剩右上角唤回小按钮（彻底关闭后的恒定入口，DEC-006）；
 * mini     概要小卡：阶段 + 第几步 + 接下来写/等你拍板 + 最新 1 条产物（DEC-005），
 *          生成中带旋转标记；点卡面展开；
 * expanded 完整浮窗：进度（八步 + 修订/拍板徽标 + 接下来写）+ 索引四组
 *          （大纲/人物/物品/世界观），点条目跳全屏面板深读并定位。
 * 窄窗口（<1280px）锁定 mini 且禁止展开（DEC-011）；浮窗态不持久化，
 * 刷新回默认 mini（避免「找不到」）。
 * 数据全部来自 contentStore（bootstrap 全量喂入 + 生成中全量轮询），
 * 这里只做视图。
 */
import { useEffect, useState } from "react";
import { useContentStore } from "@/stores/content";
import { useExecutionStore } from "@/stores/execution";
import { useWorkspaceStore } from "@/stores/workspace";
import { useWorkrailStore, type ArtifactKey } from "@/stores/workrail";
import { refreshWorkspaceContent } from "@/lib/usePanelPolling";
import {
  PIPELINE_STEPS,
  REWORK_STAGE_LABEL,
  activeStepIndex,
  isStepDone,
  phaseDisplayName,
} from "@/lib/pipeline-steps";

const NARROW_QUERY = "(max-width: 1279px)";

/* ── 图标（lucide 风格描边 SVG，对齐 ZCode 的浮窗/面板图标语言）── */

/** 唤回按钮：右侧面板图标（作品面板的入口隐喻） */
function PanelRightIcon({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect width="16" height="18" x="2" y="3" rx="2" />
      <path d="M15 3v18" />
    </svg>
  );
}

/** 收起按钮：双箭头朝右（面板收向右侧，ZCode 同款语义） */
function ChevronsRightIcon({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m6 17 5-5-5-5" />
      <path d="m13 17 5-5-5-5" />
    </svg>
  );
}

/** 关闭按钮：× */
function XIcon({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </svg>
  );
}

function outlineEntryLabel(rel: string): string {
  return rel.replace(/^outline\//, "").replace(/\.md$/, "");
}

/** 索引条目：key 用于跳转定位与交付高亮 */
type RailEntry = {
  key: ArtifactKey;
  label: string;
};

export function WorkRail() {
  const floatMode = useWorkrailStore((s) => s.floatMode);
  const setFloatMode = useWorkrailStore((s) => s.setFloatMode);
  const highlightKeys = useWorkrailStore((s) => s.highlightKeys);
  const pollError = useWorkrailStore((s) => s.pollError);

  const outlineFiles = useContentStore((s) => s.outlineFiles);
  const pipeline = useContentStore((s) => s.outlinePipeline);
  const characters = useContentStore((s) => s.characters);
  const objects = useContentStore((s) => s.objects);
  const worldviewMarkdown = useContentStore((s) => s.worldviewMarkdown);
  const loading = useExecutionStore((s) => s.loading);
  const activeWorkspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);

  // 索引组展开态（仅 expanded 态消费；mini/expanded 分支切换不重挂，展开态保留）
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({
    outline: true,
    characters: false,
    objects: false,
    worldview: false,
  });

  // 窄窗口锁定 mini（仅 resize 边沿触发，不覆盖用户操作；拉宽后可重新展开）
  const [narrow, setNarrow] = useState(() =>
    typeof window.matchMedia === "function" ? window.matchMedia(NARROW_QUERY).matches : false,
  );
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(NARROW_QUERY);
    const sync = () => {
      setNarrow(mq.matches);
      if (mq.matches) useWorkrailStore.getState().setFloatMode("mini");
    };
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  const fileRels = outlineFiles.map((f) => f.rel);
  const isPipeline = pipeline?.kind === "pipeline";
  const activeIdx = isPipeline ? activeStepIndex(pipeline, fileRels) : -1;
  const awaitingCount = isPipeline ? Object.keys(pipeline.awaiting ?? {}).length : 0;
  const activeStep = activeIdx >= 0 ? PIPELINE_STEPS[activeIdx] : null;

  const hasAnyContent =
    outlineFiles.length > 0 || characters.length > 0 || objects.length > 0 || !!worldviewMarkdown.trim();

  // 最新 1 条关键产物：数据模型无产物时间戳，按列表序取最新——
  // 大纲文件按步序落盘，末项即最近完成；无大纲时回退人物/物品/世界观（DEC-005）
  const latestArtifact =
    outlineFiles.length > 0
      ? outlineEntryLabel(outlineFiles[outlineFiles.length - 1].rel)
      : characters.length > 0
        ? characters[characters.length - 1].name || characters[characters.length - 1].filename
        : objects.length > 0
          ? objects[objects.length - 1].name || objects[objects.length - 1].filename
          : worldviewMarkdown.trim()
            ? "世界观设定"
            : null;

  // ── closed：只剩唤回小按钮（DEC-006）──
  if (floatMode === "closed") {
    return (
      <button
        type="button"
        className="work-rail-fab"
        onClick={() => setFloatMode("mini")}
        aria-label="展开作品浮窗"
        title="展开作品浮窗"
      >
        <PanelRightIcon />
      </button>
    );
  }

  // ── mini：概要小卡（DEC-005：进度概要 + 最新 1 条产物）──
  if (floatMode === "mini") {
    return (
      <aside className="work-rail mini" aria-label="作品">
        <button
          type="button"
          className="work-rail-mini-card"
          onClick={() => setFloatMode("expanded")}
          disabled={narrow}
          aria-label="展开作品详情"
          title={narrow ? "窗口太窄，拉宽后可展开" : "展开作品浮窗详情"}
        >
          {!pipeline ? (
            <p className="work-rail-placeholder">
              {hasAnyContent ? "暂无七步进度。" : "开始对话后，作品会在这里出现。"}
            </p>
          ) : pipeline.kind === "legacy" ? (
            <>
              <div className="work-rail-mini-head">
                <span className="work-rail-phase legacy">旧版工作区</span>
              </div>
              <p className="work-rail-mini-next">五件套修订模式</p>
            </>
          ) : (
            <>
              <div className="work-rail-mini-head">
                <span className={`work-rail-phase ${pipeline.phase}`}>{phaseDisplayName(pipeline.phase)}</span>
                {loading ? (
                  <span className="work-rail-mini-spinner" aria-label="生成中">
                    ⟳
                  </span>
                ) : null}
              </div>
              {activeStep ? (
                <p className="work-rail-mini-step">
                  第 {activeIdx + 1} 步 · {activeStep.label}
                </p>
              ) : null}
              <p className="work-rail-mini-next">
                {awaitingCount > 0 ? "⏸ 等你拍板" : `接下来写：${pipeline.next_unit}`}
              </p>
              {latestArtifact ? <p className="work-rail-mini-artifact">刚写完：{latestArtifact}</p> : null}
            </>
          )}
        </button>
        <button
          type="button"
          className="work-rail-close-toggle"
          onClick={() => setFloatMode("closed")}
          aria-label="关闭作品浮窗"
          title="关闭作品浮窗"
        >
          <XIcon />
        </button>
      </aside>
    );
  }

  // ── expanded：完整浮窗（进度 + 索引四组，点条目跳全屏面板并定位）──
  const generatingRow =
    loading && activeStep && activeStep.n <= 6 && !isStepDone(activeStep.n, fileRels)
      ? { label: `生成中：${activeStep.label}` }
      : null;

  const outlineEntries: RailEntry[] = outlineFiles.map((f) => ({
    key: `outline:${f.rel}`,
    label: outlineEntryLabel(f.rel),
  }));
  const characterEntries: RailEntry[] = characters.map((c) => ({
    key: `character:${c.filename}`,
    label: c.name || c.filename,
  }));
  const objectEntries: RailEntry[] = objects.map((o) => ({
    key: `object:${o.filename}`,
    label: o.name || o.filename,
  }));
  const worldviewEntries: RailEntry[] = worldviewMarkdown.trim()
    ? [{ key: "worldview", label: "世界观设定" }]
    : [];

  const groups: { id: string; title: string; count: number; entries: RailEntry[]; generating?: { label: string } | null }[] = [
    { id: "outline", title: "大纲", count: outlineEntries.length, entries: outlineEntries, generating: generatingRow },
    { id: "characters", title: "人物", count: characterEntries.length, entries: characterEntries },
    { id: "objects", title: "物品", count: objectEntries.length, entries: objectEntries },
    { id: "worldview", title: "世界观", count: worldviewEntries.length, entries: worldviewEntries },
  ];

  function handleEntryClick(key: ArtifactKey) {
    const content = useContentStore.getState();
    if (key.startsWith("outline:")) {
      useWorkspaceStore.setState({ activePanel: "outline" });
      content.setActiveOutlineRel(key.slice("outline:".length));
    } else if (key.startsWith("character:")) {
      useWorkspaceStore.setState({ activePanel: "characters" });
      useContentStore.setState({ activeCharacterFilename: key.slice("character:".length) });
    } else if (key.startsWith("object:")) {
      useWorkspaceStore.setState({ activePanel: "objects" });
      useContentStore.setState({ activeObjectFilename: key.slice("object:".length) });
    } else if (key === "worldview") {
      useWorkspaceStore.setState({ activePanel: "worldview" });
    }
  }

  return (
    <aside className="work-rail" aria-label="作品">
      <div className="work-rail-head">
        <span className="work-rail-title">作品</span>
        <div className="work-rail-head-actions">
          <button
            type="button"
            className="work-rail-collapse-toggle"
            onClick={() => setFloatMode("mini")}
            aria-label="收起作品浮窗"
            title="收起作品浮窗"
          >
            <ChevronsRightIcon />
          </button>
          <button
            type="button"
            className="work-rail-close-toggle"
            onClick={() => setFloatMode("closed")}
            aria-label="关闭作品浮窗"
            title="关闭作品浮窗"
          >
            <XIcon />
          </button>
        </div>
      </div>

      <div className="work-rail-body">
        {pollError ? (
          <div className="work-rail-error" role="alert">
            <span>状态获取失败</span>
            <button
              type="button"
              onClick={() => {
                if (activeWorkspaceId) void refreshWorkspaceContent(activeWorkspaceId);
              }}
            >
              重试
            </button>
          </div>
        ) : null}

        {/* 进度区：八步 + 徽标 + 接下来写 */}
        <div className="work-rail-progress" aria-label="创作进度">
          {!pipeline ? (
            <p className="work-rail-placeholder">
              {hasAnyContent ? "暂无七步进度。" : "开始对话后，作品会在这里出现。"}
            </p>
          ) : pipeline.kind === "legacy" ? (
            <>
              <div className="work-rail-progress-head">
                <span className="work-rail-phase legacy">旧版工作区</span>
              </div>
              <p className="work-rail-next">五件套修订模式</p>
            </>
          ) : (
            <>
              <div className="work-rail-progress-head">
                <span className={`work-rail-phase ${pipeline.phase}`}>{phaseDisplayName(pipeline.phase)}</span>
                {pipeline.rework ? (
                  <span className="work-rail-rework" title={`工单 ${pipeline.rework.ticket}`}>
                    ⟲ {REWORK_STAGE_LABEL[pipeline.rework.stage] ?? pipeline.rework.stage}
                  </span>
                ) : null}
              </div>
              <ol className="work-rail-steps">
                {PIPELINE_STEPS.map((s) => {
                  const done = isStepDone(s.n, fileRels);
                  const isActive = s.n === activeIdx;
                  return (
                    <li key={s.n} className={done ? "done" : isActive ? "active" : "todo"}>
                      <span className="work-rail-step-mark">{done ? "✓" : isActive ? "●" : "○"}</span>
                      <span className="work-rail-step-name">{s.label}</span>
                      <span className="work-rail-step-hint">{s.hint}</span>
                    </li>
                  );
                })}
              </ol>
              <p className="work-rail-next">
                {awaitingCount > 0 ? "⏸ 等你拍板" : `接下来写：${pipeline.next_unit}`}
              </p>
            </>
          )}
        </div>

        {/* 索引区：四组，点条目跳全屏面板并定位 */}
        <div className="work-rail-groups">
          {groups.map((g) => (
            <div className="work-rail-group" key={g.id}>
              <button
                type="button"
                className="work-rail-group-head"
                aria-expanded={!!openGroups[g.id]}
                onClick={() => setOpenGroups((s) => ({ ...s, [g.id]: !s[g.id] }))}
              >
                <span className="work-rail-group-title">{g.title}</span>
                <span className="work-rail-group-count">{g.count}</span>
                <span className="work-rail-group-caret">{openGroups[g.id] ? "▴" : "▾"}</span>
              </button>
              {openGroups[g.id] ? (
                g.entries.length || g.generating ? (
                  <ul className="work-rail-entries">
                    {g.entries.map((e) => (
                      <li key={e.key} className={highlightKeys.has(e.key) ? "fresh" : ""}>
                        <button type="button" onClick={() => handleEntryClick(e.key)} title={e.label}>
                          <span className="work-rail-entry-done">✓</span>
                          <span className="work-rail-entry-label">{e.label}</span>
                        </button>
                      </li>
                    ))}
                    {g.generating ? (
                      <li className="generating">
                        <span className="work-rail-entry-generating">⟳</span>
                        <span className="work-rail-entry-label">{g.generating.label}</span>
                      </li>
                    ) : null}
                  </ul>
                ) : (
                  <p className="work-rail-group-empty">还没有</p>
                )
              ) : null}
            </div>
          ))}
        </div>
      </div>
    </aside>
  );
}
