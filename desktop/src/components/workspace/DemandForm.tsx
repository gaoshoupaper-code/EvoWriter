import { FormEvent, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  DEMAND_REQUIRED_FIELDS,
  isDemandValid,
  normalizeDemandFields,
  type DemandFields,
} from "../../lib/demand";
import { readLastDemandFields } from "../../lib/session-persist";

type DemandFormProps = {
  onSubmit: (fields: DemandFields) => Promise<void>;
  disabled?: boolean;
  submitting?: boolean;
};

const EMPTY_FIELDS: DemandFields = {
  platform: "番茄",
  category: "玄幻",
  premise: "",
  goldenFinger: "",
};

/**
 * 需求表单——首次生成的唯一入口（REQ-20261015-161200 FR-007，DEC-007）。
 *
 * 4 项：平台/品类（默认值可直接用）+ 两项必填；填完直接进三步流程
 * （定世界观 → 定总纲和卷纲 → 定章纲），每步停下拍板。
 * 大纲产出后本表单退场，修订走 ChatPanel 对话入口。
 *
 * FR-001（REQ-20260909-224433 沿革）：挂载时预填上次提交的记忆（按账号隔离）；
 * 用户已动手填写则不打扰；「清空」一键回到空白表单。
 */
export function DemandForm({ onSubmit, disabled, submitting }: DemandFormProps) {
  const [fields, setFields] = useState<DemandFields>(EMPTY_FIELDS);
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const dirtyRef = useRef(false);

  // 预填是异步读盘，回来时用户可能已在输入——dirty 则放弃覆盖；
  // 旧记忆缺赛道键时合并默认值（normalizeDemandFields）
  useEffect(() => {
    let cancelled = false;
    void readLastDemandFields().then((saved) => {
      if (cancelled || !saved || dirtyRef.current) return;
      setFields(normalizeDemandFields(saved));
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
          填两项必填即可开始——每步停下等你拍板：
          定世界观 → 定总纲和卷纲 → 定章纲（埋钩子回收钩子）。
        </p>
      </div>

      <div className="demand-field-row">
        <label className="demand-field">
          <span className="demand-field-label">平台</span>
          <input
            className="thread-input"
            value={fields.platform}
            onChange={(e) => updateField("platform", e.target.value)}
            placeholder="番茄（默认）"
            disabled={busy}
          />
        </label>
        <label className="demand-field">
          <span className="demand-field-label">品类</span>
          <input
            className="thread-input"
            value={fields.category}
            onChange={(e) => updateField("category", e.target.value)}
            placeholder="玄幻（默认）"
            disabled={busy}
          />
        </label>
      </div>

      <label className="demand-field demand-field-required">
        <span className="demand-field-label">一句话点子</span>
        <textarea
          className={`thread-input demand-textarea${fieldError("premise") ? " demand-field-error" : ""}`}
          value={fields.premise}
          onChange={(e) => updateField("premise", e.target.value)}
          onBlur={() => setTouched((t) => ({ ...t, premise: true }))}
          placeholder="主角是谁 + 核心困境 + 独特抓手（爽点钩子）"
          rows={3}
          disabled={busy}
          autoFocus
        />
      </label>

      <label className="demand-field demand-field-required">
        <span className="demand-field-label">金手指</span>
        <textarea
          className={`thread-input demand-textarea${fieldError("goldenFinger") ? " demand-field-error" : ""}`}
          value={fields.goldenFinger}
          onChange={(e) => updateField("goldenFinger", e.target.value)}
          onBlur={() => setTouched((t) => ({ ...t, goldenFinger: true }))}
          placeholder="能力与边界（能做什么、不能做什么、代价或限制）"
          rows={3}
          disabled={busy}
        />
      </label>

      <div className="demand-form-actions">
        <Button
          className="send-button min-h-[46px] rounded-[14px] px-5 text-sm font-black bg-gradient-to-br from-[var(--coral)] to-[var(--gold)] shadow-lg hover:shadow-xl hover:-translate-y-px transition-all"
          type="submit"
          disabled={!valid || busy}
          title={valid ? undefined : "请先填写两项必填字段"}
        >
          {submitting ? "提交中" : "生成大纲"}
        </Button>
      </div>
    </form>
  );
}
