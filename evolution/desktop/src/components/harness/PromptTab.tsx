import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type {
  AgentDiff,
  HarnessElementView,
  Hunk,
  PromptFileInfo,
} from "@/lib/api";
import type { SaveManualAsset } from "@/lib/manual-assets-api";
import { AgentBadge } from "./AgentBadge";
import { AssetEditSheet } from "./AssetEditSheet";
import { PromptDiffViewer } from "./PromptDiffViewer";

/**
 * Prompt Tab：按 agent 分段展示系统提示词的完整文件面。
 *
 * - 清单版本（prompt_files）：常驻骨架 + 段规程逐文件卡片，全部可折叠展开审核
 * - 旧版本（无 prompt_files）：回退单 body 全文展示（历史兼容）
 * - Tab 内独立"显示 diff"开关（D18）：基础 prompt 的行级 diff 高亮（D15）不变
 * - 生产版可编辑：保存到 Prompt 与 Skills 共用的手动草稿
 */
export function PromptTab({
  agents,
  diffs,
  onSave,
  busy = false,
}: {
  agents: HarnessElementView[];
  diffs: Map<string, AgentDiff> | null;
  onSave?: SaveManualAsset;
  busy?: boolean;
}) {
  const [showDiff, setShowDiff] = useState(false);
  const [editing, setEditing] = useState<{ role: string; file: PromptFileInfo } | null>(null);

  // 无 diff 数据时禁用开关（首版本 / 无变化版本）
  const hasAnyPromptDiff = diffs
    ? agents.some((a) => diffs.get(a.name)?.prompt)
    : false;

  return (
    <div>
      <div className="diff-toggle-bar">
        <div
          className={`diff-toggle ${showDiff && hasAnyPromptDiff ? "on" : ""}`}
          onClick={() => hasAnyPromptDiff && setShowDiff(!showDiff)}
          role="switch"
          aria-checked={showDiff && hasAnyPromptDiff}
          style={!hasAnyPromptDiff ? { opacity: 0.4, cursor: "not-allowed" } : undefined}
        />
        <span>显示升级差异{!hasAnyPromptDiff && "（本版本无 prompt 变更）"}</span>
      </div>

      {agents.map((agent) => {
        const promptDiff = diffs?.get(agent.name)?.prompt ?? null;
        return (
          <div key={agent.name} className="element-agent-group">
            <div className="element-agent-group-head">
              <AgentBadge name={agent.name} />
              <h4>系统提示词</h4>
              {showDiff && promptDiff && (
                <span className="upgrade-change-desc" style={{ fontSize: "var(--text-ui-sm)" }}>
                  <span className="diff-tag-add">+{promptDiff.summary.added}</span>
                  {" / "}
                  <span className="diff-tag-del">-{promptDiff.summary.removed}</span>
                  {" 行"}
                </span>
              )}
            </div>

            {agent.prompt_files?.length ? (
              agent.prompt_files.map((file) => (
                <PromptFileCard
                  key={file.path}
                  file={file}
                  hunks={file.kind === "base" ? promptDiff?.hunks ?? null : null}
                  showDiff={showDiff}
                  onEdit={onSave ? () => setEditing({ role: agent.name, file }) : undefined}
                  busy={busy}
                />
              ))
            ) : (
              <PromptBody
                body={agent.prompt.body}
                hunks={promptDiff?.hunks ?? null}
                showDiff={showDiff}
              />
            )}
          </div>
        );
      })}

      {editing && (
        <AssetEditSheet
          title={editing.file.title}
          content={editing.file.body}
          disabled={!onSave || busy}
          onClose={() => setEditing(null)}
          onSave={async (content) => {
            await onSave?.({ role: editing.role, operation: "prompt", path: editing.file.path, content });
          }}
        />
      )}
    </div>
  );
}

/** 单个提示词文件卡：头 = 标题 + 类型标 + 路径 + 编辑，体 = 全文 / diff 高亮 */
function PromptFileCard({
  file,
  hunks,
  showDiff,
  onEdit,
  busy,
}: {
  file: PromptFileInfo;
  hunks: Hunk[] | null;
  showDiff: boolean;
  onEdit?: () => void;
  busy: boolean;
}) {
  const [open, setOpen] = useState(true);

  return (
    <div className="skill-card prompt-file-card">
      <div className="skill-card-head">
        <button
          type="button"
          className="prompt-file-toggle"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
        >
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span className="skill-card-name">{file.title}</span>
          <span className={`prompt-kind-tag ${file.kind}`}>
            {file.kind === "base" ? "常驻骨架" : "段规程"}
          </span>
          <span className="skill-path">{file.path}</span>
        </button>
        {onEdit && (
          <button type="button" className="prompt-file-edit" onClick={onEdit} disabled={busy}>
            编辑
          </button>
        )}
      </div>
      <div className="skill-card-body">
        {open &&
          (showDiff && hunks ? (
            <PromptDiffViewer hunks={hunks} />
          ) : (
            <pre className="prompt-full">{file.body}</pre>
          ))}
      </div>
    </div>
  );
}

/** 旧版本回退：单 body 全文 / diff 高亮 */
function PromptBody({
  body,
  hunks,
  showDiff,
}: {
  body: string;
  hunks: Hunk[] | null;
  showDiff: boolean;
}) {
  if (!body) return <p className="prompt-empty">（无 prompt）</p>;

  if (showDiff && hunks) {
    return <PromptDiffViewer hunks={hunks} />;
  }
  return <pre className="prompt-full">{body}</pre>;
}
