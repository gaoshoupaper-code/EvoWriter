import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ChevronDown, ChevronRight } from "lucide-react";
import type {
  AgentDiff,
  HarnessElementView,
  Hunk,
  PromptDraftView,
  PromptFileInfo,
} from "@/lib/api";
import { getPromptDrafts, releasePromptDraft, savePromptDraft } from "@/lib/api";
import { AgentBadge } from "./AgentBadge";
import { PromptDiffViewer } from "./PromptDiffViewer";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";

/**
 * Prompt Tab：按 agent 分段展示系统提示词的完整文件面。
 *
 * - 清单版本（prompt_files）：常驻骨架 + 段规程逐文件卡片，全部可折叠展开审核
 * - 旧版本（无 prompt_files）：回退单 body 全文展示（历史兼容）
 * - Tab 内独立"显示 diff"开关（D18）：基础 prompt 的行级 diff 高亮（D15）不变
 * - 编辑流（清单版本）：卡片「编辑」打开草稿 Sheet——保存写工作区草稿，
 *   顶部「发版上线」走 commit → 门禁 → 晋级生产，新版本号回调父页刷新
 */
export function PromptTab({
  agents,
  diffs,
  onReleased,
}: {
  agents: HarnessElementView[];
  diffs: Map<string, AgentDiff> | null;
  onReleased?: (version: number) => void;
}) {
  const [showDiff, setShowDiff] = useState(false);
  const [draft, setDraft] = useState<PromptDraftView | null>(null);
  const [editing, setEditing] = useState<{ path: string; title: string } | null>(null);

  // 无 diff 数据时禁用开关（首版本 / 无变化版本）
  const hasAnyPromptDiff = diffs
    ? agents.some((a) => diffs.get(a.name)?.prompt)
    : false;

  // 编辑能力只对清单版本（有 prompt_files）开放；草稿态拉取失败不阻断浏览
  const editable = agents.some((a) => a.prompt_files?.length);

  useEffect(() => {
    if (!editable) return;
    let cancelled = false;
    getPromptDrafts()
      .then((d) => !cancelled && setDraft(d))
      .catch(() => !cancelled && setDraft(null));
    return () => {
      cancelled = true;
    };
  }, [editable]);

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

      {editable && (
        <DraftBar
          draft={draft}
          onDirtyChange={setDraft}
          onReleased={onReleased}
        />
      )}

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
                  onEdit={editable ? () => setEditing({ path: file.path, title: file.title }) : undefined}
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

      <PromptEditSheet
        editing={editing}
        draft={draft}
        onClose={() => setEditing(null)}
        onSaved={(d) => setDraft(d)}
      />
    </div>
  );
}

/** 工作区草稿状态栏 + 一键发版 */
function DraftBar({
  draft,
  onDirtyChange,
  onReleased,
}: {
  draft: PromptDraftView | null;
  onDirtyChange: (d: PromptDraftView) => void;
  onReleased?: (version: number) => void;
}) {
  const [note, setNote] = useState("");
  const [releasing, setReleasing] = useState(false);

  const release = async () => {
    setReleasing(true);
    try {
      const result = await releasePromptDraft(note);
      toast.success(`已发版 v${result.version}，executor 热加载完成`);
      setNote("");
      onReleased?.(result.version);
      onDirtyChange(await getPromptDrafts());
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "发版失败");
    } finally {
      setReleasing(false);
    }
  };

  return (
    <div className="prompt-draft-bar">
      <span className={`prompt-draft-status ${draft?.dirty ? "dirty" : ""}`}>
        {draft == null
          ? "草稿状态读取中…"
          : draft.dirty
            ? "⚠ 草稿已修改（未发版，线上仍是旧版）"
            : `草稿干净 @ ${draft.head.slice(0, 9)}`}
      </span>
      <input
        className="prompt-draft-note"
        placeholder="版本说明（可空）"
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <button
        className="prompt-draft-release"
        disabled={!draft?.dirty || releasing}
        onClick={release}
      >
        {releasing ? "发版中…" : "发版上线"}
      </button>
    </div>
  );
}

/** 单个提示词文件卡：头 = 标题 + 类型标 + 路径 + 编辑，体 = 全文 / diff 高亮 */
function PromptFileCard({
  file,
  hunks,
  showDiff,
  onEdit,
}: {
  file: PromptFileInfo;
  hunks: Hunk[] | null;
  showDiff: boolean;
  onEdit?: () => void;
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
          <button type="button" className="prompt-file-edit" onClick={onEdit}>
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

/** 草稿编辑 Sheet：编辑工作区 live 文件（非当前展示版本的快照） */
function PromptEditSheet({
  editing,
  draft,
  onClose,
  onSaved,
}: {
  editing: { path: string; title: string } | null;
  draft: PromptDraftView | null;
  onClose: () => void;
  onSaved: (d: PromptDraftView) => void;
}) {
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  // 初始化只做一次（按 path 记账）：等草稿态到位后填入，之后不再覆盖用户输入
  const [inited, setInited] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    if (!editing || inited === editing.path) return;
    if (draft == null) return; // 草稿态未到先等——不拿版本快照冒充草稿
    const fromDraft = draft.files.find((f) => f.path === editing.path)?.body;
    setContent(fromDraft ?? "");
    setMissing(fromDraft == null);
    setInited(editing.path);
  }, [editing, draft, inited]);

  const save = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      const result = await savePromptDraft(editing.path, content);
      toast.success(`草稿已保存：${result.path}（记得「发版上线」才生效）`);
      onSaved(await getPromptDrafts());
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Sheet open={!!editing} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right">
        <SheetHeader>
          <SheetTitle>编辑草稿 · {editing?.title}</SheetTitle>
        </SheetHeader>
        <div className="prompt-edit-body">
          <p className="text-dim">
            保存写入工作区草稿（基线 {draft?.head?.slice(0, 9) ?? "…"}），线上不生效；
            全部改完后点「发版上线」走门禁晋级。
          </p>
          {editing && inited === editing.path && missing && (
            <p className="skill-load-error">⚠ 工作区没有该文件（可能在看历史版本）——保存会被拒。</p>
          )}
          {draft == null && (
            <p className="skill-load-error">⚠ 草稿状态不可用，暂时无法编辑。</p>
          )}
          <textarea
            className="prompt-textarea"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            spellCheck={false}
          />
          <div className="prompt-edit-actions">
            <button
              type="button"
              onClick={save}
              disabled={saving || !editing || inited !== editing.path}
            >
              {saving ? "保存中…" : "保存草稿"}
            </button>
            <button type="button" onClick={onClose} disabled={saving}>
              取消
            </button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
