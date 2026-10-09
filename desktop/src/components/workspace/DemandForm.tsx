import { FormEvent, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  DEMAND_REQUIRED_FIELDS,
  isDemandValid,
  type DemandFields,
} from "../../lib/demand";
import { readLastDemandFields } from "../../lib/session-persist";

type DemandFormProps = {
  onSubmit: (fields: DemandFields) => Promise<void>;
  disabled?: boolean;
  submitting?: boolean;
};

const EMPTY_FIELDS: DemandFields = {
  genre: "",
  premise: "",
  protagonist: "",
  focus: "",
  stylePrefs: "",
};

/**
 * 需求表单——首次生成的唯一入口（FR-002 / DEC-013）。
 *
 * 必填三项 + 选填两项；提交后模板化渲染 demand.md 交给故事专家，
 * 专家先就关键盲点澄清再构建（REQ-20261009-224433，取代旧「一口气生成」）。
 * 大纲产出后本表单退场，修订走 ChatPanel 对话入口（FR-004）。
 *
 * FR-001（REQ-20261009-224433）：挂载时预填上次提交的记忆（按账号隔离）；
 * 用户已动手填写则不打扰；「清空」一键回到空白表单。
 */
export function DemandForm({ onSubmit, disabled, submitting }: DemandFormProps) {
  const [fields, setFields] = useState<DemandFields>(EMPTY_FIELDS);
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const dirtyRef = useRef(false);

  // 预填是异步读盘，回来时用户可能已在输入——dirty 则放弃覆盖
  useEffect(() => {
    let cancelled = false;
    void readLastDemandFields().then((saved) => {
      if (cancelled || !saved || dirtyRef.current) return;
      setFields(saved);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const valid = isDemandValid(fields);
  const busy = disabled || submitting;
  const hasContent = Object.values(fields).some((v) => v.length > 0);

  function updateField(key: keyof DemandFields, value: string) {
    dirtyRef.current = true;
    setFields((f) => ({ ...f, [key]: value }));
  }

  function clearFields() {
    dirtyRef.current = true;
    setFields(EMPTY_FIELDS);
    setTouched({});
  }

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
        <div className="demand-form-heading-row">
          <h3>创作需求</h3>
          <button
            className="demand-form-clear"
            type="button"
            onClick={clearFields}
            disabled={busy || !hasContent}
            title="清空全部字段，回到空白表单"
          >
            清空
          </button>
        </div>
        <p className="demand-form-desc">
          填写三项必填即可开始——故事专家会先就关键盲点与你确认方向，
          拍板后再生成大纲三件套（故事线 / 人物 / 世界观），着急可跳过。
        </p>
      </div>

      <label className="demand-field demand-field-required">
        <span className="demand-field-label">题材 / 类型</span>
        <input
          className={`thread-input${fieldError("genre") ? " demand-field-error" : ""}`}
          value={fields.genre}
          onChange={(e) => updateField("genre", e.target.value)}
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
          onChange={(e) => updateField("premise", e.target.value)}
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
          onChange={(e) => updateField("protagonist", e.target.value)}
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
          onChange={(e) => updateField("focus", e.target.value)}
          placeholder="例：重人物弧光与关系张力；世界观点到为止"
          disabled={busy}
        />
      </label>

      <label className="demand-field">
        <span className="demand-field-label">风格偏好（选填）</span>
        <input
          className="thread-input"
          value={fields.stylePrefs}
          onChange={(e) => updateField("stylePrefs", e.target.value)}
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
