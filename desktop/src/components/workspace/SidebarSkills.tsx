/**
 * 产物树内「技能」可折叠节点（REQ-20261025-205943 FR-004/DEC-006；
 * 2026-10-26 修订：从独立分组迁入产物树，位于主题上方，点击展开）。
 *
 * 头行「技能」默认收起（角标＝全书已启用总数）；展开后是四段行＋角标
 * （该段已启用条数，0 不显示数字），点段行弹该段技能浮层。浮层经
 * createPortal 挂 body＋fixed 定位——侧栏 nav 是滚动容器（overflow
 * 裁剪），行内 absolute 浮层会被剪成不可见。浮层承担全部管理：勾选启用
 * ＋新建/编辑/删除（弹窗表单）；任何当前阶段下都可操作任意段（DEC-010）。
 * 保存的启用列表始终是全书合集（服务端整书替换契约）。
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { BookOpen, ChevronRight, Layers, ListTree, Pencil, Plus, ScrollText, Sparkles, Trash2, X, type LucideIcon } from "lucide-react";
import { toast } from "sonner";
import {
  createWritingSkill,
  updateWritingSkill,
  type WritingSkill,
  type WritingStage,
} from "@/lib/api";
import { SKILL_STAGES } from "@/lib/pipeline-steps";
import { useWorkspaceStore } from "@/stores/workspace";
import { MAX_PER_STAGE, useWritingSkillsStore } from "@/stores/writingSkills";

const STAGE_ICON: Record<WritingStage, LucideIcon> = {
  outline: ScrollText,
  volume: Layers,
  chapter: ListTree,
  text: BookOpen,
};

const MAX_CONTENT = 20000;

/** 新建/编辑弹窗（管理入口迁入侧栏，2026-10-25 修订） */
type DialogMode = { kind: "new"; stage: WritingStage } | { kind: "edit"; skill: WritingSkill };

function WritingSkillDialog({ mode, onClose }: { mode: DialogMode; onClose: () => void }) {
  const loadSkills = useWritingSkillsStore((s) => s.loadSkills);
  const loadEnabled = useWritingSkillsStore((s) => s.loadEnabled);
  const [stage, setStage] = useState<WritingStage>(mode.kind === "new" ? mode.stage : mode.skill.stage);
  const [name, setName] = useState(mode.kind === "edit" ? mode.skill.name : "");
  const [description, setDescription] = useState(mode.kind === "edit" ? mode.skill.description : "");
  const [content, setContent] = useState(mode.kind === "edit" ? mode.skill.content : "");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function handleSubmit() {
    if (submitting) return;
    if (!name.trim() || !description.trim() || !content.trim()) {
      toast.error("名称、说明、指令正文都不能为空。");
      return;
    }
    if (content.trim().length > MAX_CONTENT) {
      toast.error(`指令正文不能超过 ${MAX_CONTENT} 字。`);
      return;
    }
    setSubmitting(true);
    try {
      const data = { name: name.trim(), description: description.trim(), content: content.trim(), stage };
      if (mode.kind === "new") {
        await createWritingSkill(data);
        toast.success("技能已创建。");
      } else {
        await updateWritingSkill(mode.skill.skill_id, data);
        const label = SKILL_STAGES.find((s) => s.phase === stage)?.label ?? "该段";
        toast.success(`技能已更新，下一条${label}消息即用新内容。`);
        // 编辑可能改了阶段：刷新当前书启用列表，角标即时准确
        const wsId = useWritingSkillsStore.getState().enabledWorkspaceId;
        if (wsId) await loadEnabled(wsId).catch(() => {});
      }
      await loadSkills();
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败。");
    } finally {
      setSubmitting(false);
    }
  }

  const contentLen = content.trim().length;
  const contentOver = contentLen > MAX_CONTENT;

  return createPortal(
    <div className="modal-overlay skill-dialog-overlay" role="presentation" onMouseDown={onClose}>
      <section
        className="modal-content skill-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="skill-dialog-title"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <header className="skill-dialog-head">
          <h2 className="modal-title" id="skill-dialog-title">
            {mode.kind === "new" ? "新建写作技能" : `编辑「${mode.skill.name}」`}
          </h2>
          <button type="button" className="skill-dialog-close" onClick={onClose} aria-label="关闭">
            <X size={15} aria-hidden="true" />
          </button>
        </header>
        <div className="skill-dialog-form">
          <label className="auth-field">
            <span>适用阶段</span>
            <select
              className="auth-input"
              value={stage}
              onChange={(e) => setStage(e.target.value as WritingStage)}
              disabled={submitting}
            >
              {SKILL_STAGES.map(({ phase, label }) => (
                <option key={phase} value={phase}>{label}</option>
              ))}
            </select>
          </label>
          <label className="auth-field">
            <span>名称</span>
            <input
              className="auth-input" value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="如：武侠打斗描写" disabled={submitting}
            />
          </label>
          <label className="auth-field">
            <span>一句话说明</span>
            <input
              className="auth-input" value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="如：侧重招式细节与节奏" disabled={submitting}
            />
          </label>
          <label className="auth-field">
            <span>指令正文</span>
            <textarea
              className="auth-input wskill-content" value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="写给 AI 的具体要求，如：打斗时先写招式名，再写身体反应……"
              disabled={submitting}
            />
          </label>
          <p className={`wskill-form-tip${contentOver ? " error-copy" : ""}`}>
            {contentLen}/{MAX_CONTENT} 字 · 仅在「{SKILL_STAGES.find((s) => s.phase === stage)?.label}」阶段生效，与表达铁律冲突时以铁律为准
          </p>
        </div>
        <div className="modal-actions">
          <button className="modal-button modal-cancel" type="button" onClick={onClose} disabled={submitting}>
            取消
          </button>
          <button
            className="modal-button modal-primary" type="button"
            onClick={() => void handleSubmit()} disabled={submitting || contentOver}
          >
            {submitting ? "保存中…" : mode.kind === "new" ? "保存技能" : "更新技能"}
          </button>
        </div>
      </section>
    </div>,
    document.body,
  );
}

export function SidebarSkills() {
  const workspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const skills = useWritingSkillsStore((s) => s.skills);
  const enabled = useWritingSkillsStore((s) => s.enabled);
  const enabledWs = useWritingSkillsStore((s) => s.enabledWorkspaceId);
  const loadSkills = useWritingSkillsStore((s) => s.loadSkills);
  const loadEnabled = useWritingSkillsStore((s) => s.loadEnabled);
  const saveEnabled = useWritingSkillsStore((s) => s.saveEnabled);
  const removeSkill = useWritingSkillsStore((s) => s.removeSkill);

  const [expanded, setExpanded] = useState(false);
  const [openStage, setOpenStage] = useState<WritingStage | null>(null);
  const [dialog, setDialog] = useState<DialogMode | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const [popStyle, setPopStyle] = useState<{ top: number; left: number } | null>(null);

  // 角标随书常驻：切书即拉本书启用列表（陈旧响应在 store 内丢弃）
  useEffect(() => {
    if (!workspaceId || enabledWs === workspaceId) return;
    loadEnabled(workspaceId).catch(() => {
      // 弱网下角标暂缺，不阻塞写作
    });
  }, [workspaceId, enabledWs, loadEnabled]);

  // 展开时拉最新库列表（勾选与管理面要看到最新技能）
  useEffect(() => {
    if (!expanded) return;
    loadSkills().catch(() => toast.error("写作技能加载失败。"));
  }, [expanded, loadSkills]);

  // 浮层定位：按触发按钮视口坐标 fixed 定位（portal 挂 body，不受侧栏
  // 滚动容器裁剪），并夹在视口内
  useLayoutEffect(() => {
    if (!openStage || !triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const left = Math.min(rect.right + 8, window.innerWidth - 312);
    const top = Math.min(rect.top, window.innerHeight - 220);
    setPopStyle({ top: Math.max(8, top), left: Math.max(8, left) });
  }, [openStage]);

  // 点击浮层外 / 滚动 / 改窗口尺寸时关闭（fixed 定位不跟随滚动，直接关）
  useEffect(() => {
    if (!openStage) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (rootRef.current?.contains(t) || popoverRef.current?.contains(t)) return;
      setOpenStage(null);
    };
    const onCloseScroll = () => setOpenStage(null);
    document.addEventListener("mousedown", onDown);
    window.addEventListener("scroll", onCloseScroll, true);
    window.addEventListener("resize", onCloseScroll);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("scroll", onCloseScroll, true);
      window.removeEventListener("resize", onCloseScroll);
    };
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

  async function handleDelete(sk: WritingSkill) {
    if (!confirm(`确定删除技能「${sk.name}」？所有启用它的书都会自动解除绑定。`)) return;
    try {
      await removeSkill(sk.skill_id);
      toast.success("已删除，相关书籍已自动解除绑定。");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败。");
    }
  }

  return (
    <div className="sidebar-skills" ref={rootRef}>
      {/* 头行：可折叠「技能」节点（2026-10-26 修订——产物树内、主题上方） */}
      <button
        type="button"
        className={`sidebar-skill-toggle${expanded ? " open" : ""}`}
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
      >
        <ChevronRight size={12} className="sidebar-skill-chevron" aria-hidden="true" />
        <Sparkles size={13} className="sidebar-tree-icon" aria-hidden="true" />
        <span>技能</span>
        {enabled.length > 0 ? (
          <span className="sidebar-skill-badge">{enabled.length}</span>
        ) : null}
      </button>
      {expanded
        ? SKILL_STAGES.map(({ phase, label }) => {
            const Icon = STAGE_ICON[phase];
            const stageEnabled = enabled.filter((sk) => sk.stage === phase);
            const open = openStage === phase;
            return (
              <div className="sidebar-skill-row" key={phase}>
                <button
                  ref={open ? triggerRef : null}
                  type="button"
                  className={`sidebar-skill-item${open ? " active" : ""}`}
                  aria-expanded={open}
                  onClick={() => setOpenStage(open ? null : phase)}
                >
                  <Icon size={12} aria-hidden="true" />
                  <span>{label}</span>
                  {stageEnabled.length > 0 ? (
                    <span className="sidebar-skill-badge">{stageEnabled.length}</span>
                  ) : null}
                </button>
              </div>
            );
          })
        : null}
      {openStage && popStyle
        ? createPortal(
            <div
              className="sidebar-skill-popover"
              ref={popoverRef}
              role="dialog"
              aria-label={`${SKILL_STAGES.find((s) => s.phase === openStage)?.label}技能`}
              style={{ position: "fixed", top: popStyle.top, left: popStyle.left }}
            >
              {(() => {
                const label = SKILL_STAGES.find((s) => s.phase === openStage)?.label ?? "";
                const stageEnabled = enabled.filter((sk) => sk.stage === openStage);
                const stageSkills = skills.filter((sk) => sk.stage === openStage);
                return (
                  <>
                    <header className="writing-skills-popover-head">
                      <strong>{label}技能</strong>
                      <span className="writing-skills-count">
                        {stageEnabled.length}/{MAX_PER_STAGE} 已启用
                      </span>
                    </header>
                    {stageSkills.length === 0 ? (
                      <p className="writing-skills-empty">
                        还没有{label}技能。点下方「新建技能」，如「伏笔编织法」。
                      </p>
                    ) : (
                      <ul className="writing-skills-list">
                        {stageSkills.map((sk) => {
                          const checked = stageEnabled.some((e) => e.skill_id === sk.skill_id);
                          return (
                            <li key={sk.skill_id} className="sidebar-skill-entry">
                              <label className={`writing-skills-item ${checked ? "checked" : ""}`}>
                                <input
                                  type="checkbox"
                                  checked={checked}
                                  onChange={(e) => void toggle(openStage, label, sk.skill_id, e.target.checked)}
                                />
                                <span className="writing-skills-item-name">{sk.name}</span>
                                <span className="writing-skills-item-desc" title={sk.description}>
                                  {sk.description}
                                </span>
                              </label>
                              <span className="sidebar-skill-entry-actions">
                                <button
                                  type="button" className="sidebar-skill-entry-btn"
                                  onClick={() => setDialog({ kind: "edit", skill: sk })}
                                  aria-label={`编辑 ${sk.name}`} title="编辑"
                                >
                                  <Pencil size={12} aria-hidden="true" />
                                </button>
                                <button
                                  type="button" className="sidebar-skill-entry-btn danger"
                                  onClick={() => void handleDelete(sk)}
                                  aria-label={`删除 ${sk.name}`} title="删除"
                                >
                                  <Trash2 size={12} aria-hidden="true" />
                                </button>
                              </span>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                    <div className="sidebar-skill-popover-foot">
                      <button
                        type="button" className="sidebar-skill-new"
                        onClick={() => setDialog({ kind: "new", stage: openStage })}
                      >
                        <Plus size={12} aria-hidden="true" />
                        新建{label}技能
                      </button>
                    </div>
                    <p className="writing-skills-hint">仅在{label}阶段生效；与表达铁律冲突时以铁律为准。</p>
                  </>
                );
              })()}
            </div>,
            document.body,
          )
        : null}
      {dialog ? <WritingSkillDialog mode={dialog} onClose={() => setDialog(null)} /> : null}
    </div>
  );
}
