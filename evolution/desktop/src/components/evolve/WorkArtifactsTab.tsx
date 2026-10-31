import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronLeft, ChevronRight, Eye, FileText, RefreshCw } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import {
  getEvolveAgentArtifacts,
  getEvolveAgentArtifactContent,
  getEvolveAgentArtifactRevisionContent,
  listEvolveAgents,
  type EvolveAgent,
  type EvolveAgentArtifactsResponse,
  type EvolveArtifactRevision,
  type WritingArtifactCategory,
} from "@/lib/api";

const PAGE_SIZE = 100;
const CATEGORY_OPTIONS: { value: WritingArtifactCategory | ""; label: string }[] = [
  { value: "", label: "全部类别" },
  { value: "theme", label: "主题" },
  { value: "outline", label: "总纲" },
  { value: "style", label: "文风" },
  { value: "volume_outline", label: "卷纲" },
  { value: "chapter_outline", label: "章纲" },
  { value: "chapter_text", label: "正文" },
];

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function isExpired(revision: EvolveArtifactRevision): boolean {
  return Boolean(revision.deleted_at) || Boolean(
    revision.expires_at && new Date(revision.expires_at).getTime() <= Date.now(),
  );
}

function formatBytes(value: number | null): string {
  if (value === null) return "未知大小";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export default function WorkArtifactsTab({
  selectedAgentId,
  onSelectedAgentChange,
  canReadContent,
}: {
  selectedAgentId: string | null;
  onSelectedAgentChange: (agentId: string | null) => void;
  canReadContent: boolean;
}) {
  const [agents, setAgents] = useState<EvolveAgent[]>([]);
  const [category, setCategory] = useState<WritingArtifactCategory | "">("");
  const [fileOffset, setFileOffset] = useState(0);
  const [revisionOffset, setRevisionOffset] = useState(0);
  const [refreshRevision, setRefreshRevision] = useState(0);
  const [selectionValidated, setSelectionValidated] = useState(false);
  const requestRevision = useRef(0);
  const [result, setResult] = useState<EvolveAgentArtifactsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [agentsError, setAgentsError] = useState<string | null>(null);
  const [openFile, setOpenFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [openRevision, setOpenRevision] = useState<string | null>(null);
  const [revisionContent, setRevisionContent] = useState<string | null>(null);
  const [contentError, setContentError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listEvolveAgents(true).then((response) => {
      if (!cancelled) {
        setAgents(response.agents);
        const savedAgentExists = response.agents.some((agent) => agent.agent_id === selectedAgentId);
        if (response.agents.length > 0 && !savedAgentExists) {
          onSelectedAgentChange(response.agents[0].agent_id);
        } else if (response.agents.length === 0 && selectedAgentId) {
          onSelectedAgentChange(null);
        }
        setSelectionValidated(true);
      }
    }).catch((reason: unknown) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
    });
    return () => { cancelled = true; };
  }, [onSelectedAgentChange, selectedAgentId]);

  useEffect(() => {
    if (!selectedAgentId) {
      setResult(null);
      return;
    }
    let cancelled = false;
    const revision = ++requestRevision.current;
    setLoading(true);
    setError(null);
    setContentError(null);
    setOpenFile(null);
    setFileContent(null);
    setOpenRevision(null);
    setRevisionContent(null);
    getEvolveAgentArtifacts(selectedAgentId, {
      ...(category ? { category } : {}),
      offset: fileOffset,
      limit: PAGE_SIZE,
      revisionOffset,
      revisionLimit: PAGE_SIZE,
    }).then((response) => {
      if (!cancelled && requestRevision.current === revision) setResult(response);
    }).catch((reason: unknown) => {
      if (!cancelled && requestRevision.current === revision) {
        setResult(null);
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    }).finally(() => {
      if (!cancelled && requestRevision.current === revision) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [selectedAgentId, category, fileOffset, revisionOffset, refreshRevision]);

  const selectedAgent = agents.find((agent) => agent.agent_id === selectedAgentId);
  const files = result?.current.files ?? [];
  const revisionGroups = useMemo(() => result?.revisions ?? [], [result]);

  async function toggleCurrent(path: string) {
    const selectedAgentAtRequest = selectedAgentId;
    setContentError(null);
    if (openFile === path) {
      setOpenFile(null);
      setFileContent(null);
      return;
    }
    setOpenFile(path);
    setOpenRevision(null);
    setRevisionContent(null);
    if (!canReadContent) return;
    setFileContent(null);
    try {
      const response = await getEvolveAgentArtifactContent(selectedAgentAtRequest!, path);
      if (selectedAgentId === selectedAgentAtRequest) setFileContent(response.content);
    } catch (reason) {
      if (selectedAgentId !== selectedAgentAtRequest) return;
      setOpenFile(null);
      setContentError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function toggleRevision(revision: EvolveArtifactRevision) {
    const selectedAgentAtRequest = selectedAgentId;
    setContentError(null);
    if (openRevision === revision.artifact_revision_id) {
      setOpenRevision(null);
      setRevisionContent(null);
      return;
    }
    setOpenRevision(revision.artifact_revision_id);
    setOpenFile(null);
    setFileContent(null);
    if (!canReadContent || isExpired(revision)) return;
    try {
      const response = await getEvolveAgentArtifactRevisionContent(
        selectedAgentAtRequest!, revision.artifact_revision_id,
      );
      if (selectedAgentId === selectedAgentAtRequest) setRevisionContent(response.content);
    } catch (reason) {
      if (selectedAgentId !== selectedAgentAtRequest) return;
      setOpenRevision(null);
      setContentError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  return (
    <div className="work-artifacts">
      <header className="work-artifacts-header">
        <div>
          <h2>作品产物</h2>
          <p>当前文件来自作品目录；修订历史来自运行记录。</p>
        </div>
        <button
          className="work-artifacts-refresh"
          type="button"
          disabled={!selectedAgentId || loading}
          onClick={() => {
            setFileOffset(0);
            setRevisionOffset(0);
            setRefreshRevision((value) => value + 1);
          }}
          title="刷新产物"
          aria-label="刷新产物"
        >
          <RefreshCw size={16} />
        </button>
      </header>

      <div className="work-artifacts-controls">
        <label>
          <span>进化 Agent</span>
          <select
            value={selectedAgentId ?? ""}
            onChange={(event) => {
              onSelectedAgentChange(event.target.value || null);
              setFileOffset(0);
              setRevisionOffset(0);
            }}
          >
            {agents.length === 0 && <option value="">暂无进化 Agent</option>}
            {agents.map((agent) => (
              <option key={agent.agent_id} value={agent.agent_id}>
                {agent.name} · {agent.workspace_id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>文件类别</span>
          <select
            value={category}
            onChange={(event) => {
              setCategory(event.target.value as WritingArtifactCategory | "");
              setFileOffset(0);
            }}
          >
            {CATEGORY_OPTIONS.map((option) => (
              <option key={option.value || "all"} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        {selectedAgent && (
          <span className="work-artifacts-bound-work">绑定作品：{selectedAgent.workspace_id}</span>
        )}
      </div>

      {loading && <div className="work-artifacts-state">加载作品产物…</div>}
      {error && <div className="work-artifacts-error" role="alert">读取作品产物失败：{error}</div>}
      {error && !selectedAgentId && <div className="work-artifacts-error" role="alert">无法加载进化 Agent：{error}</div>}
      {selectionValidated && !selectedAgentId && !error && (
        <div className="work-artifacts-state">没有可查看的绑定作品。</div>
      )}
      {!loading && result?.current.kind === "unreachable" && (
        <div className="work-artifacts-error" role="alert">
          当前文件暂时无法读取：{result.current.error ?? "executor 不可达"}。修订历史仍可查看。
        </div>
      )}
      {result?.current.kind === "legacy" && (
        <div className="work-artifacts-state" role="status">
          当前文件清单仅支持五段式作品；已捕获的 Markdown 修订历史仍可查看。
        </div>
      )}
      {!loading && result?.current.kind === "missing" && (
        <div className="work-artifacts-error" role="status">作品已删除；历史修订仍可查看。</div>
      )}

      {result && (
        <div className="work-artifacts-columns">
          <section className="work-artifact-section">
            <header className="work-artifact-section-header">
              <h3>当前文件</h3>
              <span>{result.current.total} 个文件</span>
            </header>
            {files.length === 0 ? (
              <p className="work-artifacts-state">当前页没有文件。</p>
            ) : files.map((file) => (
              <article className="work-artifact-file" key={file.path}>
                <button type="button" className="work-artifact-row" onClick={() => void toggleCurrent(file.path)}>
                  <FileText size={15} aria-hidden="true" />
                  <span className="work-artifact-path">{file.path}</span>
                  <span className="work-artifact-size">{formatBytes(file.size_bytes)}</span>
                  <Eye size={15} aria-hidden="true" />
                  <ChevronDown size={14} aria-hidden="true" />
                </button>
                {openFile === file.path && (
                  <div className="work-artifact-content">
                    {canReadContent ? fileContent === null ? <span>读取当前文件…</span> : (
                      <div className="prose-doc"><ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{fileContent}</ReactMarkdown></div>
                    ) : <span>完整作品正文仅超级管理员可查看。</span>}
                  </div>
                )}
              </article>
            ))}
            <div className="work-artifacts-pagination">
              <button type="button" disabled={fileOffset === 0 || loading} onClick={() => setFileOffset(Math.max(0, fileOffset - PAGE_SIZE))} aria-label="上一页文件">
                <ChevronLeft size={16} />
              </button>
              <span>{result.current.total === 0 ? 0 : fileOffset + 1}–{Math.min(fileOffset + files.length, result.current.total)} / {result.current.total}</span>
              <button type="button" disabled={fileOffset + files.length >= result.current.total || loading} onClick={() => setFileOffset(fileOffset + PAGE_SIZE)} aria-label="下一页文件">
                <ChevronRight size={16} />
              </button>
            </div>
          </section>

          <section className="work-artifact-section">
            <header className="work-artifact-section-header">
              <h3>运行修订</h3>
              <span>{result.revision_total} 条</span>
            </header>
            {revisionGroups.length === 0 ? (
              <p className="work-artifacts-state">暂无已摄入的文本修订。</p>
            ) : revisionGroups.map((group) => (
              <article className="work-artifact-revision-group" key={group.logical_key}>
                <header className="work-artifact-revision-group-header">
                  <strong>{group.logical_key}</strong>
                  <span>{group.revision_count} 次修订</span>
                </header>
                {group.revisions.map((revision) => (
                  <div className="work-artifact-revision" key={revision.artifact_revision_id}>
                    <button
                      className="work-artifact-row"
                      type="button"
                      disabled={isExpired(revision)}
                      onClick={() => void toggleRevision(revision)}
                      title={isExpired(revision) ? "修订正文已过期" : "查看修订正文"}
                    >
                      <span className="work-artifact-revision-id">{revision.artifact_revision_id}</span>
                      <span>{formatDate(revision.created_at)}</span>
                      <span>harness@{revision.harness_version?.slice(0, 8) ?? "?"}</span>
                      <span>{isExpired(revision) ? "正文已过期" : formatBytes(revision.size_bytes)}</span>
                    </button>
                    {openRevision === revision.artifact_revision_id && (
                      <div className="work-artifact-content">
                        {canReadContent ? revisionContent === null ? <span>读取修订正文…</span> : (
                          <div className="prose-doc"><ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{revisionContent}</ReactMarkdown></div>
                        ) : <span>完整作品正文仅超级管理员可查看。</span>}
                      </div>
                    )}
                  </div>
                ))}
              </article>
            ))}
            <div className="work-artifacts-pagination">
              <button type="button" disabled={revisionOffset === 0 || loading} onClick={() => setRevisionOffset(Math.max(0, revisionOffset - PAGE_SIZE))} aria-label="上一页修订">
                <ChevronLeft size={16} />
              </button>
              <span>{result.revision_total === 0 ? 0 : revisionOffset + 1}–{Math.min(revisionOffset + result.revision_limit, result.revision_total)} / {result.revision_total}</span>
              <button type="button" disabled={revisionOffset + result.revision_limit >= result.revision_total || loading} onClick={() => setRevisionOffset(revisionOffset + PAGE_SIZE)} aria-label="下一页修订">
                <ChevronRight size={16} />
              </button>
            </div>
          </section>
        </div>
      )}
      {contentError && <div className="work-artifacts-error" role="alert">{contentError}</div>}
    </div>
  );
}
