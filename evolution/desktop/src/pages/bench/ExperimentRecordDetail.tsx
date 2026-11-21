import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import { ArtifactFiles, EvidenceValues, ModelIdentity, SampleScoreDetails } from "@/components/bench/ExperimentEvidence";
import { ArtifactReader } from "@/components/bench/ExperimentArtifacts";
import { getExperimentPackage } from "@/lib/api";
import { sampleActivitySection } from "@/lib/experiment-record-traces";
import { EXPERIMENT_STATUS, PHASE_LABELS, type ExperimentInput, type ExperimentSample } from "@/lib/experiment-types";
import { ExperimentDetailPage, ExperimentRecordTrace, experimentRunning, useExperimentActivity, useExperimentPage } from "./ExperimentDetailPage";

const sections = { writing: "写作详情", evaluation: "评分详情" };

export default function ExperimentRecordDetail() {
  const { experimentId = "", sampleId = "", section = "" } = useParams();
  const valid = section === "writing" || section === "evaluation";
  return valid ? <RecordContent key={[experimentId, sampleId, section].join("/")} {...{ experimentId, sampleId, section }} />
    : <ExperimentDetailPage id={experimentId} title="样本记录" sampleId={sampleId}>
      <p role="alert" className="experiment-error">无法识别详情类型。</p>
    </ExperimentDetailPage>;
}

function RecordContent({ experimentId, sampleId, section }: {
  experimentId: string; sampleId: string; section: keyof typeof sections;
}) {
  const { experiment, error, retry } = useExperimentPage(experimentId);
  const sample = experiment?.samples.find(item => item.id === sampleId);
  const writing = section === "writing";
  const awaitingScoreRetry = !writing && sample?.status === "evaluation_error" && !!sample.score_retry_at;
  const waiting = !!(experiment && experimentRunning(experiment) && sample && (writing
    ? ["pending", "running", "recovering", "interrupted", "stopping"].includes(sample.status)
    : ["scoring", "evaluation_error"].includes(sample.status)));
  const { runs, error: liveError } = useExperimentActivity(waiting && !!experiment?.can_read_content);
  const live = waiting ? runs.filter(run => sampleActivitySection(run, experimentId, sampleId) === section) : [];
  const trace = experiment && sample && <ExperimentRecordTrace title={writing ? "写作记录" : "评分记录"} prioritizeModelIO
    experiment={experiment} waiting={waiting} unavailable={liveError} live={live}
    archived={writing ? sample.trace_ids : sample.evaluation_trace_ids}
    context={{ experiment_name: experiment.name, sample_name: sample.package_title,
      operation: writing ? "generation" : "evaluation", task_type: sample.task_type, phase: sample.phase,
      round: sample.round, arm: sample.arm, repeat: sample.repeat }} />;
  return <ExperimentDetailPage id={experimentId} title={sections[section]} loading={!experiment} error={error} retry={retry}
    sampleId={sampleId} section={section} anchor={"record-" + section + "-" + sampleId}>
    {experiment && (!sample ? <p role="alert">本实验中没有这条样本记录。</p> : <>
      <header className="record-heading"><h2>{sample.package_title}</h2>
        <p>{experiment.name} · {PHASE_LABELS[sample.phase] ?? sample.phase} · {sample.arm === "baseline" ? "原版" : "第 " + sample.round + " 轮"}
          {sample.repeat != null && " · 重复 " + sample.repeat}{sample.attempt != null && " / 尝试 " + sample.attempt}
          {" · " + (EXPERIMENT_STATUS[sample.status] ?? sample.status)}</p></header>
      {trace}
      {awaitingScoreRetry ? <p role="status" className="experiment-note">等待自动重试评分。</p>
        : sample.error && (writing || sample.status !== "scoring") && <p role="alert" className="experiment-error">{experiment.can_read_content
          ? sample.error : "本次任务异常。查看完整原因需要正文读取权限。"}</p>}
      {writing ? <>
        <RunArtifacts sample={sample} allowed={experiment.can_read_content} />
        <RunEvidence sample={sample} allowed={experiment.can_read_content} />
      </> : <>
        <section aria-label="样本评分"><h2>评分结果</h2>
          {experiment.models?.judges.length ? <div className="record-judge-models"><span>评测模型</span>
            {experiment.models.judges.map((judge, index) => <ModelIdentity key={judge.id ?? index} model={judge} />)}</div>
            : <p className="experiment-note">评测模型未记录。</p>}
          {sample.scores ? <>
            <p>综合分 <strong>{sample.scores.overall?.toFixed(2) ?? "未知"} / 5</strong></p>
            {!experiment.can_read_content && <><dl>{Object.entries(sample.scores.dimensions).map(([key, score]) =>
                <div key={key}><dt>{key}</dt><dd>{score ?? "未知"} / 5</dd></div>)}</dl>
                <p className="experiment-note">查看评语需要正文读取权限。</p></>}
          </> : <p className="experiment-note">{sample.score_progress &&
            <>已完成 {sample.score_progress.completed} / {sample.score_progress.total} 份评分 · </>}尚无汇总评分。</p>}
          {experiment.can_read_content && <SampleScoreDetails sample={sample} judges={experiment.models?.judges ?? []} />}
        </section>
      </>}
    </>)}
  </ExperimentDetailPage>;
}

function RunArtifacts({ sample, allowed }: { sample: ExperimentSample; allowed: boolean }) {
  const [showFiles, setShowFiles] = useState(false);
  const files = sample.artifacts ?? {};
  const chapter = sample.target?.chapter;
  const file = sample.task_type === "chapter_text" && Object.keys(files).find(path => {
    const match = /^正文\/第(\d+)章[^/\\]*\.md$/.exec(path);
    return match && (!chapter || Number(match[1]) === chapter);
  });
  const manuscript = file ? files[file] : "";
  const otherFiles = Object.fromEntries(Object.entries(files).filter(([path]) => path !== file));
  const title = manuscript ? sample.completed ? "最终正文" : "已保存正文" : "运行产物";
  return <section aria-label={manuscript ? "正文" : "运行产物"}><h2>{title}</h2>
    {!allowed ? <p className="experiment-note">查看产物需要正文读取权限。</p> : manuscript ? <>
      <article className="prose-doc record-manuscript">
        <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{manuscript}</ReactMarkdown>
      </article>
      {!!Object.keys(otherFiles).length && <details onToggle={event => { if (event.currentTarget.open) setShowFiles(true); }}>
        <summary>查看归档文件</summary>{showFiles && <ArtifactReader files={otherFiles} />}
      </details>}
    </> : <ArtifactReader files={files} />}
  </section>;
}

function RunEvidence({ sample, allowed }: { sample: ExperimentSample; allowed: boolean }) {
  const [showInput, setShowInput] = useState(false);
  return <section aria-label="运行依据">
    <h2>运行依据</h2>
    <p>目标完成 {sample.completed ? "是" : "否"}
      {sample.source_commit && <> · 运行版本 <code>{sample.source_commit}</code></>}
      {sample.input_revision && <> · 输入版本 <code>{sample.input_revision}</code></>}</p>
    <details><summary>完成检查</summary>
      {Object.keys(sample.checks ?? {}).length ? <EvidenceValues value={sample.checks} />
        : <p className="experiment-note">暂无完成检查。</p>}
    </details>
    {sample.task_type !== "chapter_text" && (allowed
      ? <details onToggle={event => { if (event.currentTarget.open) setShowInput(true); }}>
        <summary>本题固定输入</summary>{showInput && <FixedInput sample={sample} />}</details>
      : <p className="experiment-note">查看固定输入需要正文读取权限。</p>)}
  </section>;
}

function FixedInput({ sample }: { sample: ExperimentSample }) {
  const [input, setInput] = useState<ExperimentInput | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const expected = sample.package_revision ?? sample.input_revision;
  useEffect(() => {
    let active = true;
    setError("");
    setInput(null);
    void getExperimentPackage(sample.package_id).then(data => {
      if (!active) return;
      if (!expected || data.input_revision !== expected) {
        setError("资料包版本与本次运行不同，无法核对原始输入。");
      } else {
        setInput(data.input);
      }
    }).catch(cause => {
      if (active) setError(cause instanceof Error ? cause.message : "读取固定输入失败。");
    });
    return () => { active = false; };
  }, [expected, revision, sample.package_id]);
  if (error) return <p role="alert" className="experiment-error">{error} <button type="button" className="action-link"
    onClick={() => setRevision(current => current + 1)}>重试</button></p>;
  if (!input) return <p role="status">正在读取固定输入…</p>;
  return <>
    <h3>完整需求</h3><p className="experiment-prewrap">{input.demand_md || "使用固定上游文稿"}</p>
    <h3>预设确认规则</h3><p className="experiment-prewrap">{input.confirmation_rules}</p>
    <ArtifactFiles files={input.files} />
    {!!input.skills.length && <details><summary>固定技能</summary><EvidenceValues value={input.skills} /></details>}
  </>;
}
