/**
 * ManuscriptEditor —— 正文人修编辑器（REQ-20261018-115508 FR-009，DEC-004）。
 *
 * 纯 markdown 编辑框（每章 2000-3000 字，不做富文本）：
 * 按章号 GET /workspaces/{id}/manuscript/{n} 拉正文；
 * 「保存」客户端直写（PUT，不经过 agent）；「人修定稿」同时把状态行改写为
 * 人修定稿（agent 的偏差回写前置条件，AC-011）。
 */
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";

type ManuscriptEditorProps = {
  workspaceId: string;
  chapter: number;
};

type ManuscriptState = {
  markdown: string;
  status: string; // draft | finalized
  exists: boolean;
};

export function ManuscriptEditor({ workspaceId, chapter }: ManuscriptEditorProps) {
  const [draft, setDraft] = useState("");
  const [remote, setRemote] = useState<ManuscriptState | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState(false);

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
        </div>
      </div>
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
    </div>
  );
}
