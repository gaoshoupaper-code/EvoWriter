/** 账号全局写作技能管理（REQ-20261102-135406）。 */
import { useEffect, useId, useRef, useState, type RefObject } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { BookOpen, Layers, ListTree, Pencil, Plus, ScrollText, Trash2, X, type LucideIcon } from "lucide-react";
import { toast } from "sonner";
import { createWritingSkill, updateWritingSkill, type WritingSkill, type WritingStage } from "@/lib/api";
import { SKILL_STAGES } from "@/lib/pipeline-steps";
import { MAX_PER_STAGE, useWritingSkillsStore } from "@/stores/writingSkills";

const STAGE_ICON: Record<WritingStage, LucideIcon> = { outline: ScrollText, volume: Layers, chapter: ListTree, text: BookOpen };
const MAX_CONTENT = 20000;
type EditorMode = { kind: "new"; stage: WritingStage } | { kind: "edit"; skill: WritingSkill };

function WritingSkillEditor({ mode, onClose }: { mode: EditorMode; onClose: () => void }) {
  const [stage, setStage] = useState<WritingStage>(mode.kind === "new" ? mode.stage : mode.skill.stage);
  const [name, setName] = useState(mode.kind === "edit" ? mode.skill.name : "");
  const [description, setDescription] = useState(mode.kind === "edit" ? mode.skill.description : "");
  const [content, setContent] = useState(mode.kind === "edit" ? mode.skill.content : "");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const pending = useRef(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (pending.current) return;
    if (!name.trim() || !description.trim() || !content.trim()) return setError("名称、说明、指令正文都不能为空。");
    if (content.trim().length > MAX_CONTENT) return setError(`指令正文不能超过 ${MAX_CONTENT} 字。`);
    pending.current = true;
    setSubmitting(true);
    setError("");
    try {
      const data = { name: name.trim(), description: description.trim(), content: content.trim(), stage };
      if (mode.kind === "new") await createWritingSkill(data);
      else await updateWritingSkill(mode.skill.skill_id, data);
    } catch (cause) {
      pending.current = false;
      setSubmitting(false);
      setError(cause instanceof Error ? cause.message : "保存失败，请重试。");
      return;
    }
    toast.success("技能已保存，下一次生成使用新设置。");
    onClose();
    // 写入已经成功，刷新失败不能把表单留作可重复创建的失败态。
    const store = useWritingSkillsStore.getState();
    const results = await Promise.allSettled([store.loadSkills(), store.loadEnabled()]);
    if (results.some((result) => result.status === "rejected")) toast.error("技能已保存，列表刷新失败，请重试。");
  }

  return <Dialog.Root open onOpenChange={(open) => { if (!open && !pending.current) onClose(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="modal-overlay skill-editor-overlay">
        <Dialog.Content className="modal-content skill-dialog" onEscapeKeyDown={(event) => { if (pending.current) event.preventDefault(); }} onInteractOutside={(event) => { if (pending.current) event.preventDefault(); }}>
          <header className="skill-dialog-head">
            <Dialog.Title className="modal-title">{mode.kind === "new" ? "新建写作技能" : `编辑「${mode.skill.name}」`}</Dialog.Title>
            <button className="skill-dialog-close" type="button" onClick={onClose} disabled={submitting} aria-label="关闭技能编辑"><X size={16} aria-hidden="true" /></button>
          </header>
          <Dialog.Description className="writing-skills-hint">保存到个人技能库，全部作品共用。</Dialog.Description>
          <form className="skill-dialog-form" onSubmit={(event) => void submit(event)}>
            <label className="auth-field"><span>适用阶段</span><select className="auth-input" value={stage} onChange={(event) => setStage(event.target.value as WritingStage)} disabled={submitting}>{SKILL_STAGES.map(({ phase, label }) => <option key={phase} value={phase}>{label}</option>)}</select></label>
            <label className="auth-field"><span>名称</span><input className="auth-input" value={name} onChange={(event) => setName(event.target.value)} disabled={submitting} /></label>
            <label className="auth-field"><span>一句话说明</span><input className="auth-input" value={description} onChange={(event) => setDescription(event.target.value)} disabled={submitting} /></label>
            <label className="auth-field"><span>指令正文</span><textarea className="auth-input wskill-content" value={content} onChange={(event) => setContent(event.target.value)} disabled={submitting} /></label>
            <p className={`wskill-form-tip${content.trim().length > MAX_CONTENT ? " error-copy" : ""}`}>{content.trim().length}/{MAX_CONTENT} 字 · 仅在对应阶段生效，与表达铁律冲突时以铁律为准。</p>
            {error ? <p className="error-copy" role="alert">{error}</p> : null}
            <div className="modal-actions">
              <button className="modal-button modal-cancel" type="button" onClick={onClose} disabled={submitting}>取消</button>
              <button className="modal-button modal-primary" type="submit" disabled={submitting || content.trim().length > MAX_CONTENT}>{submitting ? "保存中…" : mode.kind === "new" ? "保存技能" : "更新技能"}</button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Overlay>
    </Dialog.Portal>
  </Dialog.Root>;
}

export function WritingSkillsDialog({ onClose, returnFocusRef }: { onClose: () => void; returnFocusRef?: RefObject<HTMLButtonElement | null> }) {
  const id = useId();
  const skills = useWritingSkillsStore((s) => s.skills);
  const enabled = useWritingSkillsStore((s) => s.enabled);
  const pendingStages = useWritingSkillsStore((s) => s.pendingStages);
  const candidates = useWritingSkillsStore((s) => s.candidates);
  const loading = useWritingSkillsStore((s) => s.skillsLoading || s.enabledLoading || !s.enabledLoaded);
  const loadError = useWritingSkillsStore((s) => s.skillsError || s.enabledError);
  const [stage, setStage] = useState<WritingStage>("outline");
  const [drafts, setDrafts] = useState<Partial<Record<WritingStage, string[]>>>({});
  const [editor, setEditor] = useState<EditorMode | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const pendingOperation = useRef(false);
  const label = SKILL_STAGES.find((s) => s.phase === stage)!.label;
  const isPending = pendingStages.includes(stage);
  const stageSkills = [...new Map([...skills, ...candidates].filter((s) => s.stage === stage).map((s) => [s.skill_id, s])).values()];
  const ids = isPending ? (drafts[stage] ?? []).filter((id) => stageSkills.some((skill) => skill.skill_id === id)) : enabled.filter((s) => s.stage === stage).map((s) => s.skill_id);

  async function refresh() {
    setError("");
    const store = useWritingSkillsStore.getState();
    await Promise.allSettled([store.loadSkills(), store.loadEnabled()]);
  }
  useEffect(() => { void refresh(); }, []);

  async function save(next: string[]) {
    if (pendingOperation.current) return;
    pendingOperation.current = true;
    setSaving(true);
    setError("");
    try {
      await useWritingSkillsStore.getState().saveEnabled(stage, next);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败，请重试。");
    } finally {
      pendingOperation.current = false;
      setSaving(false);
    }
  }

  function toggle(skillId: string, checked: boolean) {
    if (pendingOperation.current) return;
    if (checked && ids.length >= MAX_PER_STAGE) return toast.error(`「${label}」阶段最多同时启用 ${MAX_PER_STAGE} 条技能。`);
    const next = checked ? [...ids, skillId] : ids.filter((value) => value !== skillId);
    if (isPending) setDrafts((current) => ({ ...current, [stage]: next }));
    else void save(next);
  }

  async function remove(skill: WritingSkill) {
    if (pendingOperation.current || !confirm(`确定删除技能「${skill.name}」？全部作品将不再使用该技能。`)) return;
    pendingOperation.current = true;
    setSaving(true);
    setError("");
    try {
      const deleted = await useWritingSkillsStore.getState().removeSkill(skill.skill_id);
      if (deleted) {
        setDrafts((current) => Object.fromEntries(Object.entries(current).map(([key, values]) => [key, values.filter((value) => value !== skill.skill_id)])));
        await useWritingSkillsStore.getState().loadEnabled();
        toast.success("技能已删除。");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "删除失败，请重试。");
    } finally {
      pendingOperation.current = false;
      setSaving(false);
    }
  }

  return <Dialog.Root open onOpenChange={(open) => { if (!open && !pendingOperation.current && !editor) onClose(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="modal-overlay">
        <Dialog.Content className="modal-content writing-skills-dialog" onCloseAutoFocus={(event) => { if (returnFocusRef) { event.preventDefault(); returnFocusRef.current?.focus(); } }} onEscapeKeyDown={(event) => { if (pendingOperation.current) event.preventDefault(); }} onInteractOutside={(event) => { if (pendingOperation.current) event.preventDefault(); }}>
          <header className="skill-dialog-head">
            <Dialog.Title className="modal-title">写作技能</Dialog.Title>
            <button className="skill-dialog-close" type="button" aria-label="关闭写作技能" onClick={onClose} disabled={saving}><X size={17} aria-hidden="true" /></button>
          </header>
          <Dialog.Description className="writing-skills-hint">个人账号配置，对全部作品生效。修改从下一次生成生效。</Dialog.Description>
          <div className="writing-skills-tabs" role="tablist" aria-label="技能阶段">
            {SKILL_STAGES.map(({ phase, label: stageLabel }) => {
              const Icon = STAGE_ICON[phase];
              const pendingStage = pendingStages.includes(phase);
              return <button key={phase} id={`${id}-${phase}`} type="button" role="tab" aria-selected={phase === stage} aria-controls={`${id}-panel`} className={`writing-skills-tab${phase === stage ? " active" : ""}`} onClick={() => { setStage(phase); setError(""); }} disabled={saving}>
                <Icon size={15} aria-hidden="true" /><span>{stageLabel}</span><small>{pendingStage ? "待选择" : `${enabled.filter((s) => s.stage === phase).length}/${MAX_PER_STAGE}`}</small>
              </button>;
            })}
          </div>
          {error || loadError ? <div className="writing-skills-error"><p role="alert">{error || loadError}</p>{loadError ? <button className="sidebar-skill-new" type="button" onClick={() => void refresh()} disabled={saving}>重试加载技能</button> : null}</div> : null}
          <section id={`${id}-panel`} role="tabpanel" aria-labelledby={`${id}-${stage}`} className="writing-skills-panel">
            {loading && !loadError ? <p className="writing-skills-empty" role="status">正在加载技能…</p> : loadError ? null : <>
              <header className="writing-skills-popover-head"><strong>{label}技能</strong><span className="writing-skills-count">{ids.length}/{MAX_PER_STAGE} {isPending ? "已选择" : "已启用"}</span></header>
              {isPending ? <p className="writing-skills-pending" role="status">旧作品的{label}技能合并后超额。请选择最多五条并保存；仅暂停{label}的新生成，其他阶段照常。</p> : null}
              {stageSkills.length ? <ul className="writing-skills-list">{stageSkills.map((skill) => <li key={skill.skill_id} className="sidebar-skill-entry">
                <label className={`writing-skills-item${ids.includes(skill.skill_id) ? " checked" : ""}`}>
                  <input type="checkbox" checked={ids.includes(skill.skill_id)} onChange={(event) => toggle(skill.skill_id, event.target.checked)} disabled={saving} />
                  <span className="writing-skills-item-copy"><span className="writing-skills-item-name">{skill.name}</span><span className="writing-skills-item-desc" title={skill.description}>{skill.description}</span></span>
                </label>
                <span className="sidebar-skill-entry-actions"><button className="sidebar-skill-entry-btn" type="button" aria-label={`编辑 ${skill.name}`} onClick={() => setEditor({ kind: "edit", skill })} disabled={saving}><Pencil size={13} aria-hidden="true" /></button><button className="sidebar-skill-entry-btn danger" type="button" aria-label={`删除 ${skill.name}`} onClick={() => void remove(skill)} disabled={saving}><Trash2 size={13} aria-hidden="true" /></button></span>
              </li>)}</ul> : <p className="writing-skills-empty">还没有{label}技能。新建技能后可在全部作品中启用。</p>}
              <div className="writing-skills-panel-actions"><button className="sidebar-skill-new" type="button" onClick={() => setEditor({ kind: "new", stage })} disabled={saving}><Plus size={14} aria-hidden="true" />新建{label}技能</button>{isPending ? <button className="modal-button modal-primary" type="button" onClick={() => void save(ids)} disabled={saving}>{saving ? "保存中…" : `保存${label}选择`}</button> : null}</div>
              <p className="writing-skills-hint">每阶段最多启用五条；与表达铁律冲突时以铁律为准。</p>
            </>}
          </section>
        </Dialog.Content>
      </Dialog.Overlay>
    </Dialog.Portal>
    {editor ? <WritingSkillEditor mode={editor} onClose={() => setEditor(null)} /> : null}
  </Dialog.Root>;
}
