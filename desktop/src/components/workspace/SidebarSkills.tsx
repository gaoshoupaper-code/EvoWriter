/**
 * 侧栏「技能」分组（REQ-20261025-205943 FR-004；DEC-006/009/010/011）。
 *
 * 四段各一行＋角标（该段已启用条数，0 不显示数字）；点行弹该段技能勾选
 * 浮层——只列归属该段的技能（界面层防线），纯勾选＋「去设置页管理」链接
 * （DEC-009）。勾选即保存；任何当前阶段下都可配任意段（DEC-010 随时可
 * 勾——服务端按当前段过滤注入，勾了别的段不会在本段生效）。保存的启用
 * 列表始终是全书四段合集（服务端契约为整书替换，跨段互不挤占）。
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { BookOpen, Layers, ListTree, ScrollText, Settings2, type LucideIcon } from "lucide-react";
import { toast } from "sonner";
import type { WritingStage } from "@/lib/api";
import { SKILL_STAGES } from "@/lib/pipeline-steps";
import { useWorkspaceStore } from "@/stores/workspace";
import { MAX_PER_STAGE, useWritingSkillsStore } from "@/stores/writingSkills";

const STAGE_ICON: Record<WritingStage, LucideIcon> = {
  outline: ScrollText,
  volume: Layers,
  chapter: ListTree,
  text: BookOpen,
};

export function SidebarSkills() {
  const navigate = useNavigate();
  const workspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const skills = useWritingSkillsStore((s) => s.skills);
  const enabled = useWritingSkillsStore((s) => s.enabled);
  const enabledWs = useWritingSkillsStore((s) => s.enabledWorkspaceId);
  const loadSkills = useWritingSkillsStore((s) => s.loadSkills);
  const loadEnabled = useWritingSkillsStore((s) => s.loadEnabled);
  const saveEnabled = useWritingSkillsStore((s) => s.saveEnabled);

  const [openStage, setOpenStage] = useState<WritingStage | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  // 角标随书常驻：切书即拉本书启用列表（陈旧响应在 store 内丢弃）
  useEffect(() => {
    if (!workspaceId || enabledWs === workspaceId) return;
    loadEnabled(workspaceId).catch(() => {
      // 弱网下角标暂缺，不阻塞写作
    });
  }, [workspaceId, enabledWs, loadEnabled]);

  // 浮层打开时拉最新库列表（勾选面要看到新建/改名的技能）
  useEffect(() => {
    if (!openStage) return;
    loadSkills().catch(() => toast.error("写作技能加载失败。"));
  }, [openStage, loadSkills]);

  // 点击浮层外关闭
  useEffect(() => {
    if (!openStage) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpenStage(null);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [openStage]);

  async function toggle(stage: WritingStage, label: string, skillId: string, checked: boolean) {
    if (!workspaceId) return;
    const stageEnabled = enabled.filter((sk) => sk.stage === stage);
    if (checked && stageEnabled.length >= MAX_PER_STAGE) {
      toast.error(`「${label}」阶段最多同时启用 ${MAX_PER_STAGE} 条技能。`);
      return;
    }
    const ids = enabled.map((sk) => sk.skill_id);
    const next = checked ? [...ids, skillId] : ids.filter((id) => id !== skillId);
    try {
      await saveEnabled(workspaceId, next);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败。");
    }
  }

  return (
    <div className="sidebar-group sidebar-skills" ref={rootRef}>
      <span className="sidebar-group-label">技能</span>
      {SKILL_STAGES.map(({ phase, label }) => {
        const Icon = STAGE_ICON[phase];
        const stageEnabled = enabled.filter((sk) => sk.stage === phase);
        const stageSkills = skills.filter((sk) => sk.stage === phase);
        return (
          <div className="sidebar-skill-row" key={phase}>
            <button
              type="button"
              className={`sidebar-skill-item${openStage === phase ? " active" : ""}`}
              aria-expanded={openStage === phase}
              onClick={() => setOpenStage(openStage === phase ? null : phase)}
            >
              <Icon size={12} aria-hidden="true" />
              <span>{label}</span>
              {stageEnabled.length > 0 ? (
                <span className="sidebar-skill-badge">{stageEnabled.length}</span>
              ) : null}
            </button>
            {openStage === phase ? (
              <div className="sidebar-skill-popover" role="dialog" aria-label={`${label}技能`}>
                <header className="writing-skills-popover-head">
                  <strong>{label}技能</strong>
                  <span className="writing-skills-count">
                    {stageEnabled.length}/{MAX_PER_STAGE} 已启用
                  </span>
                </header>
                {stageSkills.length === 0 ? (
                  <p className="writing-skills-empty">
                    还没有{label}技能。到「设置 → 写作技能」创建，如「伏笔编织法」。
                  </p>
                ) : (
                  <ul className="writing-skills-list">
                    {stageSkills.map((sk) => {
                      const checked = stageEnabled.some((e) => e.skill_id === sk.skill_id);
                      return (
                        <li key={sk.skill_id}>
                          <label className={`writing-skills-item ${checked ? "checked" : ""}`}>
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(e) => void toggle(phase, label, sk.skill_id, e.target.checked)}
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
                <button
                  type="button"
                  className="sidebar-skill-manage"
                  onClick={() => navigate("/settings")}
                >
                  <Settings2 size={12} aria-hidden="true" />
                  去设置页管理
                </button>
                <p className="writing-skills-hint">仅在{label}阶段生效；与表达铁律冲突时以铁律为准。</p>
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
