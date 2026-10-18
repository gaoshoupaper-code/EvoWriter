/**
 * WorkRail —— 右上角作品浮窗（REQ-20261018-115508 DEC-012：实时进度浮窗）。
 *
 * 与作品侧栏分工：WorkRail 看现在（当前段、第几步、接下来写/等你拍板、
 * 刚写完），侧栏翻档案（书切换 + 五段产物树，DEC-011）。
 *
 * 三态浮窗（不占 dashboard 栅格，悬浮于对话区右上角，完全固定不可拖）：
 * closed   只剩右上角唤回小按钮；
 * mini     概要小卡：段位 + 第几步 + 接下来写/等你拍板 + 最新 1 条产物，
 *          生成中带旋转标记；点卡面展开；
 * expanded 完整浮窗：五段进度 + 当前停点。
 * 窄窗口（<1280px 容器）锁定 mini 且禁止展开；浮窗态不持久化，刷新回默认 mini。
 * 数据来自 contentStore（bootstrap 全量喂入 + 生成中全量轮询），这里只做视图。
 */
import { useEffect, useState } from "react";
import { useContentStore } from "@/stores/content";
import { useExecutionStore } from "@/stores/execution";
import { useWorkspaceStore } from "@/stores/workspace";
import { useWorkrailStore } from "@/stores/workrail";
import { refreshWorkspaceContent } from "@/lib/usePanelPolling";
import {
  PIPELINE_STEPS,
  activeStepIndex,
  displayUnitLabel,
  isStepDone,
  phaseDisplayName,
} from "@/lib/pipeline-steps";

/* 对话区 = 窗口 − 侧栏 264px；容器不足 1280px 时锁定小卡，与 CSS 断点一致。 */
const NARROW_QUERY = "(max-width: 1543px)";

/* ── 图标（lucide 风格描边 SVG）── */

function PanelRightIcon({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect width="16" height="18" x="2" y="3" rx="2" />
      <path d="M15 3v18" />
    </svg>
  );
}

function ChevronsRightIcon({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m6 17 5-5-5-5" />
      <path d="m13 17 5-5-5-5" />
    </svg>
  );
}

function XIcon({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </svg>
  );
}

function artifactLabel(rel: string): string {
  return rel.replace(/\.md$/, "");
}

export function WorkRail() {
  const floatMode = useWorkrailStore((s) => s.floatMode);
  const setFloatMode = useWorkrailStore((s) => s.setFloatMode);
  const pollError = useWorkrailStore((s) => s.pollError);

  const outlineFiles = useContentStore((s) => s.outlineFiles);
  const pipeline = useContentStore((s) => s.outlinePipeline);
  const loading = useExecutionStore((s) => s.loading);
  const activeWorkspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);

  // 窄窗口锁定 mini（仅 resize 边沿触发；拉宽后可重新展开）
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
  const isPipeline = pipeline?.kind === "five";
  const activeIdx = isPipeline ? activeStepIndex(pipeline, fileRels) : -1;
  const awaitingCount = isPipeline ? Object.keys(pipeline.awaiting ?? {}).length : 0;
  const activeStep = activeIdx >= 0 ? PIPELINE_STEPS[activeIdx] : null;

  // 最新 1 条产物：文件按段序落盘，末项即最近完成
  const latestArtifact =
    outlineFiles.length > 0 ? artifactLabel(outlineFiles[outlineFiles.length - 1].rel) : null;

  // ── closed：只剩唤回小按钮 ──
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

  // ── mini：概要小卡 ──
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
            <p className="work-rail-placeholder">开始对话后，进度会在这里出现。</p>
          ) : pipeline.kind === "legacy" ? (
            <>
              <div className="work-rail-mini-head">
                <span className="work-rail-phase legacy">旧版工作区</span>
              </div>
              <p className="work-rail-mini-next">旧版结构，只读查看</p>
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
                {awaitingCount > 0 ? "⏸ 等你拍板" : `接下来写：${displayUnitLabel(pipeline.next_unit)}`}
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

  // ── expanded：完整浮窗（五段进度 + 当前停点；档案浏览走作品侧栏）──
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

        {/* 进度区：五段 + 接下来写 */}
        <div className="work-rail-progress" aria-label="创作进度">
          {!pipeline ? (
            <p className="work-rail-placeholder">开始对话后，进度会在这里出现。</p>
          ) : pipeline.kind === "legacy" ? (
            <>
              <div className="work-rail-progress-head">
                <span className="work-rail-phase legacy">旧版工作区</span>
              </div>
              <p className="work-rail-next">旧版结构，只读查看</p>
            </>
          ) : (
            <>
              <div className="work-rail-progress-head">
                <span className={`work-rail-phase ${pipeline.phase}`}>{phaseDisplayName(pipeline.phase)}</span>
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
                {awaitingCount > 0 ? "⏸ 等你拍板" : `接下来写：${displayUnitLabel(pipeline.next_unit)}`}
              </p>
              {/* FR-011 可跳转：展开态产物条目点击进产物面板查看（同源对齐第三条） */}
              {outlineFiles.length > 0 ? (
                <ul className="work-rail-artifacts" aria-label="最新产物">
                  {outlineFiles.slice(-5).reverse().map((f) => (
                    <li key={f.rel}>
                      <button
                        type="button"
                        className="work-rail-artifact"
                        title={f.rel}
                        onClick={() => {
                          useContentStore.getState().setActiveOutlineRel(f.rel);
                          useWorkspaceStore.setState({ activePanel: "artifacts" });
                        }}
                      >
                        {artifactLabel(f.rel)}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}
              {latestArtifact ? <p className="work-rail-next">刚写完：{latestArtifact}</p> : null}
            </>
          )}
        </div>
      </div>
    </aside>
  );
}
