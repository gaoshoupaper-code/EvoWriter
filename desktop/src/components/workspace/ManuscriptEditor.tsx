/**
 * ManuscriptEditor —— 正文「阅读/标注＋编辑」双模式（REQ-20261018-115508 FR-009；
 * 阅读态＋划词标注见 REQ-20261027-213246 DEC-005/FR-003）。
 *
 * 默认渲染态（可划词标注，与其他四类产物同交互）；点「编辑」切 textarea
 * 手改（保存直写 PUT，不经过 agent）；「人修定稿」改写状态行为「人修定稿」
 * （agent 的偏差回写前置条件，AC-011）。定稿锁的是编辑模式，标注不受限（AC-004）。
 */
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { apiFetch } from "@/lib/api";
import { markdownTableComponents } from "@/lib/markdown-components";
import { Button } from "@/components/ui/button";
import { AnnotationHost } from "./annotations/AnnotationHost";

type ManuscriptEditorProps = {
  workspaceId: string;
  chapter: number;
  /** 正文文件 rel（标注挂载用；缺省则阅读态不带标注交互） */
  rel?: string;
  reanchorTarget?: string | null;
  onReanchorDone?: () => void;
};

type ManuscriptState = {
  markdown: string;
  status: string; // draft | finalized
  exists: boolean;
};

export function ManuscriptEditor({
  workspaceId,
  chapter,
  rel,
  reanchorTarget,
  onReanchorDone,
}: ManuscriptEditorProps) {
  const [draft, setDraft] = useState("");
  const [remote, setRemote] = useState<ManuscriptState | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState(false);
  // DEC-005：默认阅读/标注态；切章回阅读态
  const [mode, setMode] = useState<"reading" | "editing">("reading");

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const resp = await apiFetch(`/api/workspaces/${workspaceId}/manuscript/${chapter}`);
      if (!resp.ok) {
        setLoadError(true);
        toast.error("正文读取失败");
        return;
      }
      const data = (await resp.json()) as ManuscriptState;
      setRemote(data);
      setDraft(data.markdown);
    } catch {
      setLoadError(true);
      toast.error("正文读取失败");
    } finally {
      setLoading(false);
    }
  }, [workspaceId, chapter]);

  useEffect(() => {
    setMode("reading");
    void load();
  }, [load]);

  async function save(finalize: boolean) {
    if (!draft.trim()) {
      toast.error("正文不能为空");
      return;
    }
    setSaving(true);
    try {
      const resp = await apiFetch(`/api/workspaces/${workspaceId}/manuscript/${chapter}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ markdown: draft, finalize }),
      });
      if (!resp.ok) {
        toast.error(finalize ? "定稿失败" : "保存失败");
        return;
      }
      const data = (await resp.json()) as ManuscriptState;
      // 服务器返回为权威文本（finalize 会改写状态行，C10 修复：同步回填编辑框）
      setRemote(data);
      setDraft(data.markdown);
      if (finalize) {
        toast.success(`第 ${chapter} 章已人修定稿，回到对话让陪练做偏差回写`);
      } else {
        toast.success("已保存");
      }
    } catch {
      toast.error(finalize ? "定稿失败" : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <p className="manuscript-state">读取正文中…</p>;
  }
  if (loadError) {
    return (
      <div className="manuscript-editor">
        <p className="manuscript-state">正文读取失败。</p>
        <div className="manuscript-actions">
          <Button type="button" variant="outline" onClick={() => void load()}>
            重试
          </Button>
        </div>
      </div>
    );
  }
  if (!remote?.exists) {
    return <p className="manuscript-state">该章正文尚未起草。</p>;
  }

  const finalized = remote.status === "finalized";

  return (
    <div className="manuscript-editor">
      <div className="manuscript-toolbar">
        <span className={`manuscript-status ${finalized ? "finalized" : "draft"}`}>
          {finalized ? "已人修定稿" : "初稿待修"}
        </span>
        <div className="manuscript-actions">
          {mode === "reading" ? (
            <Button
              type="button"
              variant="outline"
              disabled={finalized}
              title={finalized ? "已定稿章节不可再编辑；要改先划词标注或回对话让陪练处理" : "切到文本框手改正文"}
              onClick={() => setMode("editing")}
            >
              编辑
            </Button>
          ) : (
            <>
              <Button
                type="button"
                variant="outline"
                title="回到渲染阅读态；未保存的修改保留在本机，再点编辑可继续"
                onClick={() => setMode("reading")}
              >
                回阅读
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={saving || finalized}
                onClick={() => void save(false)}
              >
                {saving ? "保存中" : "保存"}
              </Button>
              <Button
                type="button"
                disabled={saving || finalized}
                onClick={() => void save(true)}
              >
                人修定稿
              </Button>
            </>
          )}
        </div>
      </div>

      {mode === "reading" ? (
        <div className="outline-markdown manuscript-reading">
          <AnnotationHost
            rel={rel ?? ""}
            enabled={Boolean(rel)}
            markdown={remote.markdown}
            reanchorTarget={reanchorTarget ?? null}
            onReanchorDone={onReanchorDone ?? (() => {})}
          >
            <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={markdownTableComponents}>
              {remote.markdown}
            </ReactMarkdown>
          </AnnotationHost>
        </div>
      ) : (
        <>
          <textarea
            className="manuscript-textarea"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={finalized}
            aria-label={`第 ${chapter} 章正文编辑`}
            spellCheck={false}
          />
          {finalized ? (
            <p className="manuscript-hint">
              已定稿章节不可再编辑；要改先在对话里让陪练处理。
            </p>
          ) : (
            <p className="manuscript-hint">改完保存；定稿后由陪练回写章纲偏差。</p>
          )}
        </>
      )}
    </div>
  );
}
