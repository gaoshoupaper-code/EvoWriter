/**
 * WorkRail —— 右侧常驻作品栏（REQ-20261012-202540 FR-001/FR-003，DEC-001/004/008）。
 *
 * 对话区右侧固定窄栏：顶部「进度」（七步法 0-7 + 修订/拍板徽标 + 接下来写），
 * 下方「索引」四组（大纲/人物/物品/世界观）。点条目跳全屏面板深读并定位。
 * 数据全部来自 contentStore（bootstrap 全量喂入 + 生成中全量轮询，FR-005），
 * 这里只做视图；收起态不持久化，刷新回默认展开（避免「找不到」）。
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

function outlineEntryLabel(rel: string): string {
  return rel.replace(/^outline\//, "").replace(/\.md$/, "");
}

/** 索引条目：key 用于跳转定位与交付高亮 */
type RailEntry = {
  key: ArtifactKey;
  label: string;
};

export function WorkRail() {
  const collapsed = useWorkrailStore((s) => s.collapsed);
  const toggleCollapsed = useWorkrailStore((s) => s.toggleCollapsed);
  const highlightKeys = useWorkrailStore((s) => s.highlightKeys);
  const pollError = useWorkrailStore((s) => s.pollError);

  const outlineFiles = useContentStore((s) => s.outlineFiles);
  const pipeline = useContentStore((s) => s.outlinePipeline);
  const characters = useContentStore((s) => s.characters);
  const objects = useContentStore((s) => s.objects);
  const worldviewMarkdown = useContentStore((s) => s.worldviewMarkdown);
  const loading = useExecutionStore((s) => s.loading);
  const activeWorkspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);

  // 窄窗口自动收起（可手动展开回来；仅 resize 边沿触发，不覆盖用户操作）
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(NARROW_QUERY);
    const sync = () => {
      if (mq.matches) useWorkrailStore.getState().toggleCollapsed(true);
    };
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  const fileRels = outlineFiles.map((f) => f.rel);
  const isPipeline = pipeline?.kind === "pipeline";
  const activeIdx = isPipeline ? activeStepIndex(pipeline, fileRels) : -1;
  const awaitingCount = isPipeline ? Object.keys(pipeline.awaiting ?? {}).length : 0;

  // 大纲组：已有文件（✓）+ 活跃步尚未落盘时的「生成中」占位行
  const outlineEntries: RailEntry[] = outlineFiles.map((f) => ({
    key: `outline:${f.rel}`,
    label: outlineEntryLabel(f.rel),
  }));
  const activeStep = activeIdx >= 0 ? PIPELINE_STEPS[activeIdx] : null;
  const generatingRow =
    loading && activeStep && activeStep.n <= 6 && !isStepDone(activeStep.n, fileRels)
      ? { label: `生成中：${activeStep.label}` }
      : null;

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

  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({
    outline: true,
    characters: false,
    objects: false,
    worldview: false,
  });

  const hasAnyContent =
    outlineFiles.length > 0 || characters.length > 0 || objects.length > 0 || !!worldviewMarkdown.trim();

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

  // ── 收起态：细条（进度点 + 展开按钮）──
  if (collapsed) {
    return (
      <aside className="work-rail collapsed" aria-label="作品">
        <button
          type="button"
          className="work-rail-collapse-toggle"
          onClick={() => toggleCollapsed(false)}
          aria-label="展开作品栏"
          title="展开作品栏"
        >
          «
        </button>
        <ol className="work-rail-mini-dots" aria-hidden="true">
          {PIPELINE_STEPS.map((s) => (
            <li
              key={s.n}
              className={isPipeline && s.n === activeIdx ? "active" : isPipeline && isStepDone(s.n, fileRels) ? "done" : ""}
            />
          ))}
        </ol>
      </aside>
    );
  }

  // ── 展开态 ──
  return (
    <aside className="work-rail" aria-label="作品">
      <div className="work-rail-head">
        <span className="work-rail-title">作品</span>
        <button
          type="button"
          className="work-rail-collapse-toggle"
          onClick={() => toggleCollapsed(true)}
          aria-label="收起作品栏"
          title="收起作品栏"
        >
          »
        </button>
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

        {/* 进度区：七步 + 徽标 + 接下来写（FR-002：对话标题带阶段条迁入此处） */}
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

        {/* 索引区：四组，点条目跳全屏面板并定位（FR-003） */}
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
