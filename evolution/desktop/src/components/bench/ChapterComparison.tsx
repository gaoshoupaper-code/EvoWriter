import { useMemo, useState } from "react";
import { EXPERIMENT_STATUS, PHASE_LABELS, type Experiment, type ExperimentSample } from "@/lib/experiment-types";
import { RUNTIME_DIMENSION, RuntimeScoringWeights, RuntimeScoreDetails, SampleScoreDetails } from "./ExperimentEvidence";

const versionLabel = (round: number) => round ? "第 " + round + " 轮" : "原版";
const purpose = (sample: ExperimentSample) => ["exploration", "baseline"].includes(sample.phase) ? "optimization" : sample.phase;
const pairingKey = (sample: ExperimentSample) => JSON.stringify([sample.package_id, sample.input_revision, purpose(sample), sample.repeat]);

function chapterParagraphs(sample?: ExperimentSample) {
  if (!sample?.target?.chapter) return [];
  const file = Object.entries(sample.artifacts).find(([path]) => {
    const chapter = /^正文\/第(\d+)章[^/\\]*\.md$/.exec(path);
    return chapter && Number(chapter[1]) === sample.target?.chapter;
  });
  return file?.[1].trim() ? file[1].split(/\r?\n/) : [];
}

export function ImprovementSources({ experiment, references, onTrace }: {
  experiment: Experiment; references: string[]; onTrace: (id: string) => void;
}) {
  if (!experiment.can_read_content || !references.length) return null;
  return <>{experiment.samples.filter((sample) => references.includes(sample.id)
    && experiment.settings.optimization_ids?.includes(sample.package_id)
    && ["baseline", "exploration"].includes(sample.phase)).map((sample) => <details key={sample.id}>
    <summary>依据样本 · {sample.package_title} · {versionLabel(sample.arm === "baseline" ? 0 : sample.round)}
      {" · 重复 " + sample.repeat + " / 尝试 " + sample.attempt}</summary>
    {sample.error && <p className="experiment-error">{sample.error}</p>}
    <h4>{sample.completed ? "最终正文" : "已保存正文（未完成）"}</h4>
    <p className="experiment-prewrap">{chapterParagraphs(sample).join("\n") || "尚无目标章正文。"}</p>
    {sample.scores ? <SampleScoreDetails sample={sample} judges={experiment.models?.judges ?? []} /> : <p>尚无有效评分。</p>}
    {sample.trace_ids.map((id, i) => <button key={id} className="action-link" onClick={() => onTrace(id)}>查看依据生成 Trace {i + 1}</button>)}
    {sample.evaluation_trace_ids?.map((id, i) => <button key={id} className="action-link" onClick={() => onTrace(id)}>查看依据评分 Trace {i + 1}</button>)}
  </details>)}</>;
}

// Match equal occurrences in order so repeated dialogue and moved paragraphs stay distinguishable.
function unchangedParagraphs(left: string[], right: string[]) {
  const positions = new Map<string, number[]>();
  right.forEach((line, index) => {
    const indices = positions.get(line) ?? [];
    indices.push(index);
    positions.set(line, indices);
  });
  const seen = new Map<string, number>();
  const tails: number[] = [];
  const previous: number[] = [];
  const matches: { left: number; right: number }[] = [];
  left.forEach((line, index) => {
    const occurrence = seen.get(line) ?? 0;
    seen.set(line, occurrence + 1);
    const other = positions.get(line)?.[occurrence];
    if (other == null) return;
    let low = 0, high = tails.length;
    while (low < high) {
      const mid = (low + high) >>> 1;
      if (matches[tails[mid]].right < other) low = mid + 1;
      else high = mid;
    }
    previous.push(low ? tails[low - 1] : -1);
    tails[low] = matches.length;
    matches.push({ left: index, right: other });
  });
  const leftMatches = new Set<number>(), rightMatches = new Set<number>();
  for (let i = tails.at(-1) ?? -1; i >= 0; i = previous[i]) {
    leftMatches.add(matches[i].left);
    rightMatches.add(matches[i].right);
  }
  return [leftMatches, rightMatches];
}

export function ChapterComparison({ experiment, onTrace, initialRound }: {
  experiment: Pick<Experiment, "baseline" | "can_read_content" | "judge_repeats" | "models" | "rounds" | "samples" | "scoring_rule" | "runtime_scoring_version">;
  onTrace: (id: string) => void;
  initialRound: number | null;
}) {
  const [sourceVersion, setSourceVersion] = useState(0);
  const [targetVersion, setTargetVersion] = useState<number | null>(initialRound);
  const [chosenPair, setChosenPair] = useState("");
  const [chosenAttempts, setChosenAttempts] = useState(["", ""]);
  const versions = [0, ...experiment.rounds.map((item) => item.round)];
  const rounds = [sourceVersion, targetVersion ?? versions.at(-1)!];
  function inVersion(sample: ExperimentSample, side: number) {
    if (rounds[side]) return sample.arm === "candidate" && sample.round === rounds[side];
    if (sample.arm !== "baseline") return false;
    // Regression generates fresh baseline runs alongside each candidate.
    return sample.phase === "baseline" || sample.round === rounds[1 - side];
  }
  const records = [0, 1].map((side) => versions.length === 1 && side === 1 ? [] :
    experiment.samples.filter((sample) => inVersion(sample, side)));
  const pairs = new Map(records.flat().map((sample) => [pairingKey(sample), sample]));
  const key = pairs.has(chosenPair) ? chosenPair : pairs.keys().next().value;
  const attempts = records.map((items) => items.filter((sample) => pairingKey(sample) === key)
    .sort((a, b) => b.attempt - a.attempt));
  const samples = attempts.map((items, index) => items.find((item) => item.id === chosenAttempts[index]) ?? items[0]);
  const [left, right] = samples;
  const paragraphs = useMemo(() => experiment.can_read_content ? [chapterParagraphs(left), chapterParagraphs(right)] : [[], []],
    [experiment.can_read_content, left, right]);
  const compareText = paragraphs.every((lines) => lines.length > 0) && rounds[0] !== rounds[1];
  const unchanged = useMemo(() => unchangedParagraphs(paragraphs[0], paragraphs[1]), [paragraphs]);
  const leftRuntime = left?.scores?.runtime;
  const rightRuntime = right?.scores?.runtime;
  const leftMetrics = leftRuntime ?? left?.runtime_metrics;
  const rightMetrics = rightRuntime ?? right?.runtime_metrics;
  const runtimeScoring = Boolean(experiment.runtime_scoring_version || leftRuntime || rightRuntime);
  const dimensions = [...new Set(samples.flatMap((sample) => Object.keys(sample?.scores?.dimensions ?? {})))]
    .filter((name) => !runtimeScoring || name !== RUNTIME_DIMENSION);
  function scoreRow(name: string, a?: number | null, b?: number | null) {
    const delta = a == null || b == null ? null : b - a;
    return <tr key={name}><th scope="row">{name}</th><td>{a?.toFixed(3) ?? "未评分"}</td><td>{b?.toFixed(3) ?? "未评分"}</td>
      <td>{delta == null || rounds[0] === rounds[1] ? "—" : (delta > 0 ? "+" : "") + delta.toFixed(3)}</td></tr>;
  }
  return <section className="experiment-section" aria-label="跨版本终稿对比">
    <h2>跨版本终稿对比</h2>
    <RuntimeScoringWeights rule={experiment.scoring_rule} version={experiment.runtime_scoring_version} weights={right?.scores?.weights ?? left?.scores?.weights} />
    {((experiment.scoring_rule && !experiment.runtime_scoring_version) || experiment.judge_repeats != null) && <p className="experiment-note">
      {experiment.scoring_rule && !experiment.runtime_scoring_version && <>评测规则 {experiment.scoring_rule.name} · v{experiment.scoring_rule.version}
        {experiment.judge_repeats != null && " · "}</>}
      {experiment.judge_repeats != null && <>每个模型评 {experiment.judge_repeats} 次</>}
    </p>}
    <div className="chapter-pair-controls">{["源版本", "对照版本"].map((label, index) => <label key={label}>{label}
      <select className="config-input" value={rounds[index]} disabled={versions.length === 1} onChange={(event) => {
        const version = Number(event.target.value);
        if (version === rounds[1 - index]) {
          if (!index) setTargetVersion(rounds[0]); else setSourceVersion(rounds[1]);
        }
        if (!index) setSourceVersion(version); else setTargetVersion(version);
      }}>{versions.map((round) => <option key={round} value={round}>{versionLabel(round)}</option>)}</select>
    </label>)}
      <label>题目与重复<select className="config-input" value={key ?? ""} onChange={(event) => setChosenPair(event.target.value)}>
        {Array.from(pairs, ([id, sample]) => <option key={id} value={id}>{sample.package_title} · {purpose(sample) === "optimization" ? "改进题" : PHASE_LABELS[sample.phase] ?? sample.phase}
          {" · 重复 " + sample.repeat + " · 输入 " + sample.input_revision}</option>)}
      </select></label>
    </div>
    <table className="data-table chapter-scores"><caption>{experiment.runtime_scoring_version ? "评分变化" : "质量变化"}</caption><thead><tr><th>评分项</th><th>源版本</th><th>对照版本</th><th>对照减源</th></tr></thead>
      <tbody>{scoreRow("综合分", left?.scores?.overall, right?.scores?.overall)}
        {experiment.runtime_scoring_version && scoreRow("正文质量分", left?.scores?.quality, right?.scores?.quality)}
        {dimensions.map((name) => scoreRow(name, left?.scores?.dimensions[name], right?.scores?.dimensions[name]))}
        {runtimeScoring && <>
          {scoreRow("工具分", leftMetrics?.tools?.score, rightMetrics?.tools?.score)}
          {scoreRow("Token 分", leftMetrics?.tokens?.score, rightMetrics?.tokens?.score)}
          {scoreRow("Skill 分", leftRuntime?.skill.score, rightRuntime?.skill.score)}
          {scoreRow("运行时综合分", leftRuntime?.score, rightRuntime?.score)}
        </>}</tbody></table>
    {!experiment.can_read_content && <p className="experiment-note">当前账号没有正文读取权限。</p>}
    {versions.length === 1 && <p className="experiment-note">尚无改进轮次，可先查看原版。</p>}
    <div className="chapter-columns">{rounds.slice(0, versions.length === 1 ? 1 : 2).map((version, side) => {
      const sample = samples[side];
      const round = experiment.rounds.find((item) => item.round === version);
      return <article key={side} className="chapter-side"><h3>{side ? "对照" : "源"} · {versionLabel(version)}</h3>
        <p className="experiment-cell-note">{version ? EXPERIMENT_STATUS[round?.status ?? ""] ?? round?.status : "原版基线"}
          {" · " + (version ? round?.source_commit || "尚无版本标识" : experiment.baseline.commit)}</p>
        {experiment.can_read_content && round && <>
          {round.summary && <details open><summary>改动摘要</summary><p className="experiment-prewrap">{round.summary}</p></details>}
          {round.design && <details><summary>改进依据</summary><p className="experiment-prewrap">{round.design}</p></details>}
          {round.diff && <details><summary>相对起始版本的资产差异</summary><pre className="experiment-diff">{round.diff}</pre></details>}
        </>}
        {round?.startup_check && <p>启动检查 · {round.startup_check.status === "passed" ? "通过" : "失败"}</p>}
        {round?.error && <p className="experiment-error">{experiment.can_read_content ? round.error : "本轮失败，完整原因需正文读取权限。"}</p>}
        {round?.trace_ids?.map((id, index) => <button className="action-link" key={id} onClick={() => onTrace(id)}>进化 Trace {index + 1}</button>)}
        {!sample ? <p className="experiment-note">此版本尚无同题同次的生成记录。</p> : <>
          <details><summary>生成标识</summary><p>产物所属尝试 <code>{sample.id}</code></p>
            <p>实际版本 <code>{sample.source_commit}</code></p>
            <p>输入版本 <code>{sample.input_revision}</code></p></details>
          <label>生成尝试<select className="config-input" value={sample.id} onChange={(event) => {
            const next = [...chosenAttempts]; next[side] = event.target.value; setChosenAttempts(next);
          }}>{attempts[side].map((item) => <option key={item.id} value={item.id}>尝试 {item.attempt} · {EXPERIMENT_STATUS[item.status] ?? item.status}</option>)}</select></label>
          {sample.error && <p className="experiment-error">{experiment.can_read_content ? sample.error : "本次运行异常，完整原因需正文读取权限。"}</p>}
          {sample.trace_ids.map((id, index) => <button className="action-link" key={id} onClick={() => onTrace(id)}>生成 Trace {index + 1}</button>)}
          {sample.evaluation_trace_ids?.map((id, index) => <button className="action-link" key={id} onClick={() => onTrace(id)}>评分 Trace {index + 1}</button>)}
          {!sample.scores && sample.score_progress && <p className="experiment-note">
            已完成 {sample.score_progress.completed} / {sample.score_progress.total} 份评分 · 尚无汇总评分
          </p>}
          <RuntimeScoreDetails sample={sample} showQuality={false} />
          {!experiment.can_read_content && !!experiment.runtime_scoring_version && !sample.scores && sample.score_progress?.reasons &&
            <SampleScoreDetails sample={sample} judges={experiment.models?.judges ?? []} canReadEvidence={false}
              runtimeScoringVersion={experiment.runtime_scoring_version} />}
          {experiment.can_read_content && <>
            {(sample.scores || sample.score_progress?.reasons) && <details><summary>逐项评语与依据</summary>
              <SampleScoreDetails sample={sample} judges={experiment.models?.judges ?? []}
                runtimeScoringVersion={experiment.runtime_scoring_version} /></details>}
            <h4>{sample.completed ? "最终正文" : "已保存正文（未完成）"}</h4>
            {paragraphs[side].length ? paragraphs[side].map((line, n) => <p key={n}>{!compareText || unchanged[side].has(n) ? line
              : side ? <ins>{line}</ins> : <del>{line}</del>}</p>) : <p className="experiment-note">尚无目标章正文。</p>}
          </>}
        </>}
      </article>;
    })}</div>
  </section>;
}
