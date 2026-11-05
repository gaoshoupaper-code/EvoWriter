import { useEffect, useRef, useState } from "react";
import { Bot, Square } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  getTargetTask, listTargetTasks, startTargetTask, stopTargetTask,
  type TargetTask, type TargetTaskStart, type TargetTaskType,
} from "@/lib/api";
import { refreshWorkspaceContent } from "@/lib/usePanelPolling";
import "./automatic-task.css";

const ACTIVE = new Set(["pending", "running", "recovering", "stopping"]);
const STATUS: Record<string, string> = { pending: "待运行", running: "运行中", recovering: "恢复中", completed: "已完成", failed: "失败", interrupted: "已中断", stopping: "停止中", stopped: "已停止" };
const PHASE: Record<string, string> = { theme: "主题", outline: "总纲", volume: "卷纲", chapter: "章纲", text: "正文", review: "审稿", revision: "修订" };

type Props = { workspaceId: string; threadId?: string; disabled?: boolean; onBusyChange?: (busy: boolean) => void };

export function AutomaticTaskPanel({ workspaceId, threadId, disabled = false, onBusyChange }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [kind, setKind] = useState<TargetTaskType>("volume_outlines");
  const [volumeCount, setVolumeCount] = useState("");
  const [volumeIds, setVolumeIds] = useState("");
  const [chapter, setChapter] = useState("");
  const [demand, setDemand] = useState("");
  const [rules, setRules] = useState("");
  const [task, setTask] = useState<TargetTask | null>(null);
  const [checking, setChecking] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const currentTask = useRef<TargetTask | null>(null);
  const generation = useRef(0);
  const scope = useRef(workspaceId);
  const pending = useRef(false);
  scope.current = workspaceId;
  const active = !!task && ACTIVE.has(task.status);

  useEffect(() => {
    onBusyChange?.(active);
  }, [active, onBusyChange]);
  useEffect(() => () => onBusyChange?.(false), [onBusyChange]);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    generation.current += 1; currentTask.current = null; pending.current = false;
    setTask(null); setChecking(true); setBusy(false); setError(""); setExpanded(false);
    setDemand(""); setRules(""); setVolumeIds(""); setVolumeCount(""); setChapter("");
    if (!workspaceId) { setChecking(false); return; }
    async function refresh() {
      if (!pending.current) {
        const sequence = ++generation.current;
        try {
          const previous = currentTask.current;
          const result = previous && ACTIVE.has(previous.status) ? await getTargetTask(workspaceId, previous.task_id) :
            await listTargetTasks(workspaceId).then(({ tasks }) => tasks.find((item) => ACTIVE.has(item.status)) ?? [...tasks].sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null);
          if (alive && sequence === generation.current) {
            currentTask.current = result; setTask(result); setError("");
            if (result && (ACTIVE.has(result.status) || previous?.updated_at !== result.updated_at)) void refreshWorkspaceContent(workspaceId);
          }
        } catch (cause) { if (alive && sequence === generation.current) setError(cause instanceof Error ? cause.message : "读取全自动任务失败"); }
        finally { if (alive) setChecking(false); }
      }
      if (alive) timer = setTimeout(refresh, 3000);
    }
    void refresh();
    return () => { alive = false; generation.current += 1; clearTimeout(timer); };
  }, [workspaceId]);

  async function operate(action: () => Promise<TargetTask>) {
    if (pending.current) return;
    const sequence = ++generation.current;
    pending.current = true; setBusy(true); setError("");
    try {
      const result = await action();
      if (scope.current === workspaceId && sequence === generation.current) {
        currentTask.current = result; setTask(result); setExpanded(false);
        void refreshWorkspaceContent(workspaceId);
      }
    } catch (cause) { if (scope.current === workspaceId && sequence === generation.current) setError(cause instanceof Error ? cause.message : "全自动任务操作失败"); }
    finally { if (scope.current === workspaceId && sequence === generation.current) { pending.current = false; setBusy(false); } }
  }
  function start(event: React.FormEvent) {
    event.preventDefault(); if (!workspaceId || active || disabled || checking || busy) return;
    let target: TargetTaskStart["target"];
    if (kind === "volume_outlines") {
      const count = Number(volumeCount);
      if (!Number.isSafeInteger(count) || count < 1) { setError("请填写整本书的目标卷数。"); return; }
      target = { task_type: kind, volume_ids: Array.from({ length: count }, (_, i) => i + 1) };
    } else if (kind === "chapter_outlines") {
      const ids = volumeIds.split(/[、,，\s]+/).filter(Boolean).map(Number);
      if (ids.length !== 4 || new Set(ids).size !== 4 || ids.some((id) => !Number.isSafeInteger(id) || id < 1)) {
        setError("请填写四个不同的正整数卷号。"); return;
      }
      target = { task_type: kind, volume_ids: ids.sort((a, b) => a - b) };
    } else {
      const number = Number(chapter);
      if (!Number.isSafeInteger(number) || number < 1) { setError("请填写目标章号。"); return; }
      target = { task_type: kind, volume_ids: [], chapter: number };
    }
    if (!rules.trim()) { setError("请填写预设确认规则。"); return; }
    const payload: TargetTaskStart = { target, confirmation_rules: rules.trim(), ...(threadId ? { thread_id: threadId } : {}), ...(demand.trim() ? { demand_md: demand.trim() } : {}) };
    void operate(() => startTargetTask(workspaceId, payload));
  }
  return <section className="automatic-task-panel" aria-label="全自动目标任务">
    <div className="automatic-task-heading"><button type="button" className="automatic-task-toggle" aria-expanded={expanded} disabled={!workspaceId || disabled || checking || active || busy} onClick={() => setExpanded((value) => !value)}><Bot size={15} aria-hidden="true" />全自动任务</button>
      {task && <span role="status" className={`automatic-task-status ${task.status}`}><span>{STATUS[task.status] ?? task.status}</span>{task.phase ? ` · ${PHASE[task.phase] ?? task.phase}` : ""}</span>}
      {active && <button type="button" className="automatic-task-stop" disabled={busy || task?.status === "stopping"} onClick={() => task && void operate(() => stopTargetTask(workspaceId, task.task_id))}><Square size={12} aria-hidden="true" />停止全自动任务</button>}
    </div>
    {error && <p className="automatic-task-error" role="alert">{error}</p>}
    {task?.error && task.error !== error && <p className="automatic-task-error">{task.error}</p>}
    {expanded && <form className="automatic-task-form" onSubmit={start}>
      <p className="automatic-task-note">直接使用当前作品资料，完成所选目标及段内检查。中途无需人工确认。</p>
      <label>任务目标<select value={kind} onChange={(event) => { setKind(event.target.value as TargetTaskType); setError(""); }}>
        <option value="volume_outlines">整本全部卷纲</option><option value="chapter_outlines">指定 4 卷全部章纲</option><option value="chapter_text">单章正文，含审稿修订</option></select></label>
      {kind === "volume_outlines" ? <label>全书目标卷数<input type="number" required min={1} step={1} value={volumeCount} onChange={(event) => setVolumeCount(event.target.value)} /></label> :
        kind === "chapter_outlines" ? <label>目标卷号<input required value={volumeIds} placeholder="例如：1,2,3,4" onChange={(event) => setVolumeIds(event.target.value)} /></label> :
          <label>目标章号<input type="number" required min={1} step={1} value={chapter} onChange={(event) => setChapter(event.target.value)} /></label>}
      {kind === "volume_outlines" && <label className="automatic-task-wide">完整需求（已有需求时可留空）<textarea rows={3} value={demand} onChange={(event) => setDemand(event.target.value)} /></label>}
      <label className="automatic-task-wide">预设确认规则<textarea required rows={3} value={rules} placeholder="写明可自动确认的选择。缺少必需资料时结束并说明原因。" onChange={(event) => setRules(event.target.value)} /></label>
      <div className="automatic-task-actions"><button type="submit" disabled={busy || checking || disabled}>{busy ? "启动中…" : "开始全自动任务"}</button><button type="button" onClick={() => setExpanded(false)}>收起</button></div>
    </form>}
    {task && <details className="automatic-task-evidence"><summary>任务记录 · 已保存 {Object.keys(task.artifacts).length} 份产物</summary>
      <p className="automatic-task-note">{task.completed ? "目标及必要检查已完成。" : active ? "正在执行，产物会持续保存。" : "保留已生成内容及执行结果。"} · 尝试 {task.attempts} 次</p>
      <p className="automatic-task-note">输入版本 <code>{task.input_revision}</code></p>
      {Object.entries(task.artifacts).map(([path, content]) => <details key={path}><summary>{path}</summary><div className="markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown></div></details>)}
    </details>}
  </section>;
}
