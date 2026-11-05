import { useEffect, useState } from "react";
import { listExperimentRules, saveExperimentRule, type ExperimentRule, type ExperimentRuleInput } from "@/lib/api";
import { TASK_LABELS, TASK_TYPES, type ExperimentTaskType } from "@/lib/experiment-types";
import "./experiments.css";

const draftFrom = (rule: ExperimentRule): ExperimentRuleInput => ({
  task_type: rule.task_type, name: rule.name,
  dimensions: rule.dimensions.map((dimension) => ({ ...dimension, anchors: { ...dimension.anchors } })),
  discipline_rules: [...rule.discipline_rules],
});

export function StageRules() {
  const [rules, setRules] = useState<ExperimentRule[]>([]);
  const [kind, setKind] = useState<ExperimentTaskType>("volume_outlines");
  const [selectedId, setSelectedId] = useState("");
  const [draft, setDraft] = useState<ExperimentRuleInput | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let alive = true;
    listExperimentRules().then(({ items }) => {
      if (!alive) return;
      setRules(items);
      const first = items.filter((rule) => rule.task_type === "volume_outlines").sort((a, b) => b.version - a.version)[0];
      if (first) { setSelectedId(first.id); setDraft(draftFrom(first)); }
    }).catch((cause) => { if (alive) setError(cause instanceof Error ? cause.message : "读取规则失败"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  function select(rule: ExperimentRule | undefined) {
    setSelectedId(rule?.id ?? ""); setDraft(rule ? draftFrom(rule) : null); setNotice(""); setError("");
  }
  function editDimension(index: number, update: Partial<ExperimentRuleInput["dimensions"][number]>) {
    setDraft((value) => value && ({ ...value, dimensions: value.dimensions.map((dimension, i) => i === index ? { ...dimension, ...update } : dimension) }));
  }
  async function save(event: React.FormEvent) {
    event.preventDefault(); if (!draft || saving) return;
    setSaving(true); setError(""); setNotice("");
    try {
      const result = await saveExperimentRule(draft);
      setRules((current) => [result, ...current]); setSelectedId(result.id); setDraft(draftFrom(result));
      setNotice(`已保存版本 ${result.version}。进行中的实验仍使用原规则。`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "保存规则失败"); }
    finally { setSaving(false); }
  }
  return <section className="experiment-section" aria-labelledby="stage-rules-title">
    <h2 id="stage-rules-title">三段评分规则</h2><p className="experiment-note">各项 1–5 分。保存生成新版本；已启动实验的规则保持固定。</p>
    {error && <p role="alert" className="experiment-error">{error}</p>}{notice && <p role="status">{notice}</p>}
    <div className="experiment-fields"><label>规则类型<select className="config-input" value={kind} disabled={saving || loading} onChange={(event) => {
      const next = event.target.value as ExperimentTaskType; setKind(next);
      select(rules.filter((rule) => rule.task_type === next).sort((a, b) => b.version - a.version)[0]);
    }}>{TASK_TYPES.map((type) => <option key={type} value={type}>{TASK_LABELS[type]}</option>)}</select></label>
      <label>规则版本<select className="config-input" value={selectedId} disabled={saving || loading} onChange={(event) => select(rules.find((rule) => rule.id === event.target.value))}>
        {rules.filter((rule) => rule.task_type === kind).sort((a, b) => b.version - a.version).map((rule) => <option key={rule.id} value={rule.id}>v{rule.version} · {rule.name}</option>)}
      </select></label></div>
    {loading ? <p role="status">正在加载规则…</p> : draft ? <form onSubmit={(event) => void save(event)}>
      <label className="experiment-field">规则名称<input className="config-input" required value={draft.name} disabled={saving} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
      <div className="experiment-rule-list">{draft.dimensions.map((dimension, index) => <fieldset key={dimension.key} disabled={saving}>
        <legend>{dimension.key}</legend>
        <label>判定问题<textarea className="config-input" required aria-label={`${dimension.key}评分问题`} value={dimension.question} onChange={(event) => editDimension(index, { question: event.target.value })} /></label>
        <div className="experiment-rule-anchors">{["5", "4", "3", "2", "1"].map((level) => <label key={level}>{level} 分<textarea className="config-input" required aria-label={`${dimension.key}${level}分标准`} value={dimension.anchors[level] ?? ""}
          onChange={(event) => editDimension(index, { anchors: { ...dimension.anchors, [level]: event.target.value } })} /></label>)}</div>
      </fieldset>)}</div>
      <label className="experiment-field">评分纪律（每行一条）<textarea className="config-input" required rows={4} value={draft.discipline_rules.join("\n")} disabled={saving}
        onChange={(event) => setDraft({ ...draft, discipline_rules: event.target.value.split("\n") })} /></label>
      <button type="submit" className="config-button primary" disabled={saving}>{saving ? "保存中…" : "保存为新版本"}</button>
    </form> : <p className="experiment-note">暂无该类型规则。</p>}
  </section>;
}
