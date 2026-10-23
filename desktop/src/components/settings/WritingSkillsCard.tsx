/**
 * 设置页「写作技能」板块（REQ-20261023-000000 FR-001/004，DEC-001/005/006）。
 *
 * 个人技能库 CRUD：左列表 + 右表单（复用模型服务板块的 provider-layout 布局）。
 * 三字段均必填（空白视为空），指令正文 ≤3000 字（前端先拦，服务端双保险）。
 * 勾选启用在写作页浮层（WritingSkillsPopover），本板块只管库。
 */
import { FormEvent, useEffect, useState } from "react";
import { toast } from "sonner";
import { createWritingSkill, updateWritingSkill } from "@/lib/api";
import { useWritingSkillsStore } from "@/stores/writingSkills";
import type { WritingSkill } from "@/lib/api";

const MAX_CONTENT = 20000;

type FormMode = { kind: "new" } | { kind: "edit"; skill: WritingSkill };

export function WritingSkillsCard() {
  const skills = useWritingSkillsStore((s) => s.skills);
  const skillsLoading = useWritingSkillsStore((s) => s.skillsLoading);
  const loadSkills = useWritingSkillsStore((s) => s.loadSkills);
  const removeSkill = useWritingSkillsStore((s) => s.removeSkill);

  const [mode, setMode] = useState<FormMode>({ kind: "new" });
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    loadSkills().catch(() => toast.error("写作技能加载失败。"));
  }, [loadSkills]);

  function resetForm() {
    setMode({ kind: "new" });
    setName("");
    setDescription("");
    setContent("");
  }

  function startEdit(sk: WritingSkill) {
    setMode({ kind: "edit", skill: sk });
    setName(sk.name);
    setDescription(sk.description);
    setContent(sk.content);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
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
      const data = { name: name.trim(), description: description.trim(), content: content.trim() };
      if (mode.kind === "new") {
        await createWritingSkill(data);
        toast.success("技能已创建。");
      } else {
        await updateWritingSkill(mode.skill.skill_id, data);
        toast.success("技能已更新，下一条正文消息即用新内容。");
      }
      resetForm();
      await loadSkills();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败。");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(sk: WritingSkill) {
    if (!confirm(`确定删除技能「${sk.name}」？所有启用它的书都会自动解除绑定。`)) return;
    if (submitting) return;
    setSubmitting(true);
    try {
      await removeSkill(sk.skill_id);
      toast.success("已删除，相关书籍已自动解除绑定。");
      if (mode.kind === "edit" && mode.skill.skill_id === sk.skill_id) resetForm();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败。");
    } finally {
      setSubmitting(false);
    }
  }

  const contentLen = content.trim().length;
  const contentOver = contentLen > MAX_CONTENT;

  return (
    <section className="settings-card">
      <header className="settings-card-head">
        <div className="settings-card-title">
          <h2>写作技能</h2>
          <p>自建题材/场景写作技能（如「武侠打斗描写」），写作页正文段勾选启用，每本书最多 5 条。</p>
        </div>
        <span className="settings-status ok">
          <span className="settings-status-text">共 {skills.length} 条</span>
        </span>
      </header>

      <div className="provider-layout">
        <aside className="provider-list">
          <div className="provider-list-head">
            <span>我的技能（{skills.length}）</span>
            <button type="button" className="provider-new-btn" onClick={resetForm}>+ 新建</button>
          </div>
          {skillsLoading ? (
            <p className="provider-empty">加载中…</p>
          ) : skills.length === 0 ? (
            <p className="provider-empty">还没有技能，点「新建」添加。</p>
          ) : (
            skills.map((sk) => (
              <div
                key={sk.skill_id}
                className={`provider-item ${mode.kind === "edit" && mode.skill.skill_id === sk.skill_id ? "editing" : ""}`}
              >
                <div className="provider-item-main" onClick={() => startEdit(sk)}>
                  <div className="provider-item-name">{sk.name}</div>
                  <div className="provider-item-meta" title={sk.description}>{sk.description}</div>
                </div>
                <div className="provider-item-actions">
                  <button
                    type="button"
                    className="provider-action danger"
                    onClick={() => void handleDelete(sk)}
                  >
                    删除
                  </button>
                </div>
              </div>
            ))
          )}
        </aside>

        <div className="provider-form">
          <h3>{mode.kind === "new" ? "新建技能" : `编辑「${mode.skill.name}」`}</h3>
          <form className="auth-form" onSubmit={handleSubmit}>
            <label className="auth-field">
              <span>名称</span>
              <input
                className="auth-input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="如：武侠打斗描写"
                disabled={submitting}
              />
            </label>
            <label className="auth-field">
              <span>一句话说明</span>
              <input
                className="auth-input"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="如：侧重招式细节与节奏"
                disabled={submitting}
              />
            </label>
            <label className="auth-field">
              <span>指令正文</span>
              <textarea
                className="auth-input wskill-content"
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder="写给 AI 的具体要求，如：打斗时先写招式名，再写身体反应……"
                disabled={submitting}
              />
              <p className={`wskill-form-tip${contentOver ? " error-copy" : ""}`}>
                {contentLen}/{MAX_CONTENT} 字 · 仅正文写作生效，与表达铁律冲突时以铁律为准
              </p>
            </label>
            <div className="settings-form-actions">
              {mode.kind === "edit" ? (
                <button className="settings-btn" type="button" onClick={resetForm} disabled={submitting}>
                  取消编辑
                </button>
              ) : null}
              <button
                className="settings-btn settings-btn-primary"
                type="submit"
                disabled={submitting || contentOver}
              >
                {submitting ? "保存中…" : mode.kind === "new" ? "保存技能" : "更新技能"}
              </button>
            </div>
          </form>
        </div>
      </div>
    </section>
  );
}
