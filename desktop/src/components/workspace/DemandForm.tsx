import { FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  DEMAND_REQUIRED_FIELDS,
  isDemandValid,
  type DemandFields,
} from "../../lib/demand";

type DemandFormProps = {
  onSubmit: (fields: DemandFields) => Promise<void>;
  disabled?: boolean;
  submitting?: boolean;
};

/**
 * 需求表单——首次生成的唯一入口（FR-002 / DEC-013）。
 *
 * 必填三项 + 选填两项；提交后模板化渲染 demand.md 交给故事专家，
 * 全程无中途提问（DEC-012 一口气生成）。大纲产出后本表单退场，
 * 修订走 ChatPanel 对话入口（FR-004）。
 */
export function DemandForm({ onSubmit, disabled, submitting }: DemandFormProps) {
  const [fields, setFields] = useState<DemandFields>({
    genre: "",
    premise: "",
    protagonist: "",
    focus: "",
    stylePrefs: "",
  });
  const [touched, setTouched] = useState<Record<string, boolean>>({});

  const valid = isDemandValid(fields);
  const busy = disabled || submitting;

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!valid || busy) return;
    setTouched(Object.fromEntries(DEMAND_REQUIRED_FIELDS.map((k) => [k, true])));
    if (!isDemandValid(fields)) return;
    void onSubmit(fields);
  }

  function fieldError(key: keyof DemandFields): boolean {
    return touched[key] === true && fields[key].trim().length === 0;
  }

  return (
    <form className="demand-form" onSubmit={handleSubmit} aria-label="创作需求表单">
      <div className="demand-form-heading">
        <span className="section-kicker">Brief</span>
        <h3>创作需求</h3>
        <p className="demand-form-desc">
          填写三项必填即可开始——故事专家将据此生成大纲三件套（故事线 / 人物 / 世界观），
          审查修订全程自动完成。
        </p>
      </div>

      <label className="demand-field demand-field-required">
        <span className="demand-field-label">题材 / 类型</span>
        <input
          className={`thread-input${fieldError("genre") ? " demand-field-error" : ""}`}
          value={fields.genre}
          onChange={(e) => setFields((f) => ({ ...f, genre: e.target.value }))}
          onBlur={() => setTouched((t) => ({ ...t, genre: true }))}
          placeholder="例：玄幻 · 热血升级流"
          disabled={busy}
          autoFocus
        />
      </label>

      <label className="demand-field demand-field-required">
        <span className="demand-field-label">核心创意 / 一句话卖点</span>
        <textarea
          className={`thread-input demand-textarea${fieldError("premise") ? " demand-field-error" : ""}`}
          value={fields.premise}
          onChange={(e) => setFields((f) => ({ ...f, premise: e.target.value }))}
          onBlur={() => setTouched((t) => ({ ...t, premise: true }))}
          placeholder="主角是谁 + 核心困境 + 独特抓手（爽点钩子）"
          rows={3}
          disabled={busy}
        />
      </label>

      <label className="demand-field demand-field-required">
        <span className="demand-field-label">主角与核心设定要点</span>
        <textarea
          className={`thread-input demand-textarea${fieldError("protagonist") ? " demand-field-error" : ""}`}
          value={fields.protagonist}
          onChange={(e) => setFields((f) => ({ ...f, protagonist: e.target.value }))}
          onBlur={() => setTouched((t) => ({ ...t, protagonist: true }))}
          placeholder="身份起点 / 核心欲望 / 弱点软肋 / 金手指边界（能做什么、不能做什么）"
          rows={3}
          disabled={busy}
        />
      </label>

      <label className="demand-field">
        <span className="demand-field-label">大纲侧重（选填）</span>
        <input
          className="thread-input"
          value={fields.focus}
          onChange={(e) => setFields((f) => ({ ...f, focus: e.target.value }))}
          placeholder="例：重人物弧光与关系张力；世界观点到为止"
          disabled={busy}
        />
      </label>

      <label className="demand-field">
        <span className="demand-field-label">风格偏好（选填）</span>
        <input
          className="thread-input"
          value={fields.stylePrefs}
          onChange={(e) => setFields((f) => ({ ...f, stylePrefs: e.target.value }))}
          placeholder="例：热血燃向、快节奏、避免慢热开头"
          disabled={busy}
        />
      </label>

      <div className="demand-form-actions">
        <Button
          className="send-button min-h-[46px] rounded-[14px] px-5 text-sm font-black bg-gradient-to-br from-[var(--coral)] to-[var(--gold)] shadow-lg hover:shadow-xl hover:-translate-y-px transition-all"
          type="submit"
          disabled={!valid || busy}
          title={valid ? undefined : "请先填写三项必填字段"}
        >
          {submitting ? "提交中" : "生成大纲"}
        </Button>
      </div>
    </form>
  );
}
