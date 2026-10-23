/**
 * 写作页正文段「写作技能」入口（REQ-20261023-000000 FR-004/DEC-007）。
 *
 * 仅正文段渲染（由 ChatPanel 按 pipeline.phase 判定，非正文段无入口）；
 * 角标显示本书已启用条数（0 条不显示数字）；浮层勾选即保存（≤5 条本地
 * 先拦，服务端双保险）。对话流内不插任何「已加载」提示（DEC-007）。
 */
import { useEffect, useRef, useState } from "react";
import { Sparkles } from "lucide-react";
import { toast } from "sonner";
import { useWorkspaceStore } from "@/stores/workspace";
import { MAX_ENABLED_PER_WORKSPACE, useWritingSkillsStore } from "@/stores/writingSkills";

export function WritingSkillsPopover() {
  const workspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const skills = useWritingSkillsStore((s) => s.skills);
  const enabledIds = useWritingSkillsStore((s) => s.enabledIds);
  const enabledWs = useWritingSkillsStore((s) => s.enabledWorkspaceId);
  const loadSkills = useWritingSkillsStore((s) => s.loadSkills);
  const loadEnabled = useWritingSkillsStore((s) => s.loadEnabled);
  const saveEnabled = useWritingSkillsStore((s) => s.saveEnabled);

  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // 角标随书常驻：到正文段/切书即拉本书启用列表（陈旧响应在 store 内丢弃）
  useEffect(() => {
    if (!workspaceId || enabledWs === workspaceId) return;
    loadEnabled(workspaceId).catch(() => {
      // 弱网下角标暂缺，不阻塞写作
    });
  }, [workspaceId, enabledWs, loadEnabled]);

  // 浮层打开时拉最新库列表（勾选面要看到新建/改名的技能）
  useEffect(() => {
    if (!open) return;
    loadSkills().catch(() => toast.error("写作技能加载失败。"));
  }, [open, loadSkills]);

  // 点击浮层外关闭
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  async function toggle(skillId: string, checked: boolean) {
    if (!workspaceId) return;
    const next = checked
      ? [...enabledIds, skillId]
      : enabledIds.filter((id) => id !== skillId);
    if (checked && next.length > MAX_ENABLED_PER_WORKSPACE) {
      toast.error(`每本书最多同时启用 ${MAX_ENABLED_PER_WORKSPACE} 条写作技能。`);
      return;
    }
    try {
      await saveEnabled(workspaceId, next);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败。");
    }
  }

  const enabledCount = enabledIds.length;

  return (
    <div className="writing-skills-root" ref={rootRef}>
      <button
        className="writing-skills-button"
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={`写作技能（已启用 ${enabledCount} 条）`}
        title="写作技能"
      >
        <Sparkles size={15} aria-hidden="true" />
        {enabledCount > 0 ? <span className="writing-skills-badge">{enabledCount}</span> : null}
      </button>
      {open ? (
        <div className="writing-skills-popover">
          <header className="writing-skills-popover-head">
            <strong>本书写作技能</strong>
            <span className="writing-skills-count">
              {enabledCount}/{MAX_ENABLED_PER_WORKSPACE} 已启用
            </span>
          </header>
          {skills.length === 0 ? (
            <p className="writing-skills-empty">
              还没有技能。到「设置 → 写作技能」创建，如「武侠打斗描写」「爽点节奏」。
            </p>
          ) : (
            <ul className="writing-skills-list">
              {skills.map((sk) => {
                const checked = enabledIds.includes(sk.skill_id);
                return (
                  <li key={sk.skill_id}>
                    <label className={`writing-skills-item ${checked ? "checked" : ""}`}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(e) => void toggle(sk.skill_id, e.target.checked)}
                      />
                      <span className="writing-skills-item-name">{sk.name}</span>
                      <span className="writing-skills-item-desc" title={sk.description}>
                        {sk.description}
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
          <p className="writing-skills-hint">仅正文写作生效；与表达铁律冲突时以铁律为准。</p>
        </div>
      ) : null}
    </div>
  );
}
