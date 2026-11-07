import { useEffect, useState } from "react";
import { Pencil, Check, X } from "lucide-react";
import { listExperimentRules, saveExperimentRule, type ExperimentRule, type ExperimentRuleInput } from "@/lib/api";
import { RULE_PROFILE, TASK_LABELS, TASK_TYPES, type ExperimentTaskType } from "@/lib/experiment-types";
import "./experiments.css";

const HARD_CHECKS: Record<ExperimentTaskType, string[]> = {
  volume_outlines: ["整本 3 卷齐全", "每卷规划 20 章，共覆盖第 1–60 章", "产物定稿，符合现有卷纲规程"],
  chapter_outlines: ["仅生成指定一章", "指令卡适用字段齐全", "目标章号、范围及状态合法"],
  chapter_text: ["指定单章正文完整", "真实审稿成功", "必要修订有执行证据"],
};
const draftFrom = (rule: ExperimentRule): ExperimentRuleInput => ({
  task_type: rule.task_type, name: rule.name, profile: rule.profile,
  dimensions: rule.dimensions.map((dimension) => ({ ...dimension, anchors: { ...dimension.anchors } })),
  discipline_rules: [...rule.discipline_rules],
});
const latestRule = (rules: ExperimentRule[], kind: ExperimentTaskType) => {
  const sameKind = rules.filter((rule) => rule.task_type === kind).sort((a, b) => b.version - a.version);
  return sameKind.find((rule) => rule.profile === RULE_PROFILE) ?? sameKind[0];
};

export function StageRules() {
  const [rules, setRules] = useState<ExperimentRule[]>([]);
  const [kind, setKind] = useState<ExperimentTaskType>("volume_outlines");
  const [selectedId, setSelectedId] = useState("");
  const [draft, setDraft] = useState<ExperimentRuleInput | null>(null);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let alive = true;
    listExperimentRules().then(({ items }) => {
      if (!alive) return;
      setRules(items);
      const first = latestRule(items, "volume_outlines");
      if (first) { setSelectedId(first.id); setDraft(draftFrom(first)); }
    }).catch((cause) => { if (alive) setError(cause instanceof Error ? cause.message : "读取规则失败"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);
  const selected = rules.find((rule) => rule.id === selectedId);
  const currentProfile = selected?.profile === RULE_PROFILE;
  const totalWeight = selected?.dimensions.reduce((sum, dimension) => sum + dimension.weight, 0) || 1;

  function select(rule: ExperimentRule | undefined) {
    setSelectedId(rule?.id ?? ""); setDraft(rule ? draftFrom(rule) : null);
    setEditing(false); setNotice(""); setError("");
  }
  function chooseKind(next: ExperimentTaskType) {
    setKind(next); select(latestRule(rules, next));
  }
  function editDimension(index: number, update: Partial<ExperimentRuleInput["dimensions"][number]>) {
    setDraft((value) => value && ({ ...value, dimensions: value.dimensions.map((dimension, i) => i === index ? { ...dimension, ...update } : dimension) }));
  }
  async function save(event: React.FormEvent) {
    event.preventDefault(); if (!draft || saving || !currentProfile) return;
    setSaving(true); setError(""); setNotice("");
    try {
      const result = await saveExperimentRule(draft);
      setRules((current) => [result, ...current]); setSelectedId(result.id); setDraft(draftFrom(result)); setEditing(false);
      setNotice("已保存版本 " + result.version + "。进行中的实验仍使用原规则。");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "保存规则失败"); }
    finally { setSaving(false); }
  }
  return <section className="experiment-section" aria-labelledby="stage-rules-title">
    <div className="experiment-heading"><div><h2 id="stage-rules-title">三阶段评分规则</h2>
      <p className="experiment-note">程序先检查是否完成，模型再按维度评分。各项 1–5 分，实验启动后固定规则版本。</p></div>
      <span className="experiment-count">三套独立规则</span></div>
    {error && <p role="alert" className="experiment-error">{error}</p>}{notice && <p role="status" className="experiment-positive">{notice}</p>}
    <div className="experiment-rule-stage-nav" role="tablist" aria-label="规则阶段">
      {TASK_TYPES.map((type, index) => <button key={type} type="button" role="tab" id={"rule-tab-" + type}
        aria-selected={kind === type} aria-controls="stage-rule-content" tabIndex={kind === type ? 0 : -1}
        disabled={saving || loading} onClick={() => chooseKind(type)} onKeyDown={(event) => {
          let next = index;
          if (event.key === "ArrowRight") next = (index + 1) % TASK_TYPES.length;
          else if (event.key === "ArrowLeft") next = (index + TASK_TYPES.length - 1) % TASK_TYPES.length;
          else return;
          event.preventDefault(); chooseKind(TASK_TYPES[next]); document.getElementById("rule-tab-" + TASK_TYPES[next])?.focus();
        }}><span>{TASK_LABELS[type]}</span><small>{latestRule(rules, type)?.dimensions.length ?? 0} 个评分维度</small></button>)}
    </div>
    {loading ? <p role="status">正在加载规则…</p> : selected && draft ? <div id="stage-rule-content" role="tabpanel" aria-labelledby={"rule-tab-" + kind}>
      <div className="experiment-rule-heading"><div><h3>{selected.name}</h3>
        <p className="experiment-note">{currentProfile ? "当前三阶段规则" : kind === "chapter_outlines" ? "4 卷章纲历史规则" : "历史规则，保留原始评测含义"} · v{selected.version} · {selected.dimensions.length} 个维度</p></div>
        <div className="experiment-actions"><label className="experiment-rule-version">规则版本<select className="config-input" value={selectedId} disabled={saving} onChange={(event) => select(rules.find((rule) => rule.id === event.target.value))}>
          {rules.filter((rule) => rule.task_type === kind).sort((a, b) => b.version - a.version).map((rule) => <option key={rule.id} value={rule.id}>
            {rule.profile === RULE_PROFILE ? "" : "历史 · "}v{rule.version} · {rule.name}
          </option>)}</select></label>
          {currentProfile && !editing && <button type="button" className="config-button" onClick={() => setEditing(true)}><Pencil size={14} />编辑规则</button>}
        </div>
      </div>
      {!currentProfile && <p className="experiment-note">历史规则只读，不会自动匹配到新的单章实验。</p>}
      {!editing ? <>
        <div className="experiment-hard-checks"><h4>完成检查 <span>程序判定，单独计算完成率</span></h4>
          {currentProfile ? <ul>{HARD_CHECKS[kind].map((check) => <li key={check}><Check size={14} />{check}</li>)}</ul> :
            <p>按历史实验启动时固定的目标范围与完成标准判定。</p>}</div>
        <div className="experiment-rule-read-list">{selected.dimensions.map((dimension) => <article key={dimension.key}>
          <div className="experiment-rule-dimension-heading"><h4>{dimension.key}</h4><span>{(dimension.weight / totalWeight * 100).toFixed(0)}% 权重</span></div>
          <p>{dimension.question}</p>
          <details><summary>查看 1–5 分判定标准</summary><dl className="experiment-anchor-list">
            {["5", "4", "3", "2", "1"].map((level) => <div key={level}><dt>{level} 分</dt><dd>{dimension.anchors[level]}</dd></div>)}
          </dl></details>
        </article>)}</div>
        <details className="experiment-discipline"><summary>评分纪律 · {selected.discipline_rules.length} 条</summary>
          <ol>{selected.discipline_rules.map((rule, index) => <li key={index}>{rule}</li>)}</ol></details>
      </> : <form onSubmit={(event) => void save(event)}>
        <p className="experiment-note">修改将保存为新版本，既有实验的规则保持不变。</p>
        <label className="experiment-field">规则名称<input className="config-input" required value={draft.name} disabled={saving} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
        <div className="experiment-rule-list">{draft.dimensions.map((dimension, index) => <fieldset key={dimension.key} disabled={saving}>
          <legend>{dimension.key}</legend>
          <label>判定问题<textarea className="config-input" required aria-label={dimension.key + "评分问题"} value={dimension.question} onChange={(event) => editDimension(index, { question: event.target.value })} /></label>
          <label className="experiment-weight-edit">权重<input className="config-input" type="number" min="0.001" step="any" required aria-label={dimension.key + "权重"} value={dimension.weight}
            onChange={(event) => editDimension(index, { weight: Number(event.target.value) })} /></label>
          <div className="experiment-rule-anchors">{["5", "4", "3", "2", "1"].map((level) => <label key={level}>{level} 分<textarea className="config-input" required aria-label={dimension.key + level + "分标准"} value={dimension.anchors[level] ?? ""}
            onChange={(event) => editDimension(index, { anchors: { ...dimension.anchors, [level]: event.target.value } })} /></label>)}</div>
        </fieldset>)}</div>
        <label className="experiment-field">评分纪律（每行一条）<textarea className="config-input" required rows={4} value={draft.discipline_rules.join("\n")} disabled={saving}
          onChange={(event) => setDraft({ ...draft, discipline_rules: event.target.value.split("\n") })} /></label>
        <div className="experiment-actions"><button type="submit" className="config-button primary" disabled={saving}>{saving ? "保存中…" : "保存为新版本"}</button>
          <button type="button" className="config-button" disabled={saving} onClick={() => { setDraft(draftFrom(selected)); setEditing(false); }}><X size={14} />取消编辑</button></div>
      </form>}
    </div> : <p className="experiment-empty">暂无该阶段规则，请核对规则初始化状态。</p>}
  </section>;
}
