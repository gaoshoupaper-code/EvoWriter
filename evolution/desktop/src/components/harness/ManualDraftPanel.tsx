import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { agentLabel } from "@/lib/harness-constants";
import { selectedAgent } from "@/lib/writing-agent";
import {
  discardManualAssetDraft,
  getManualAssetDraft,
  releaseManualAssetDraft,
  saveManualAssetDraft,
  type ManualAssetChange,
  type ManualAssetDraft,
  type SaveManualAsset,
} from "@/lib/manual-assets-api";
import "./manual-assets.css";

const labels = {
  prompt: "修改提示词",
  skill_edit: "修改技能",
  skill_import: "导入技能",
  skill_delete: "删除技能",
};

function observedRelease(draft: ManualAssetDraft, next: ManualAssetDraft) {
  if (next.revision !== draft.revision || next.published_version == null || next.published_version <= draft.base_version) {
    return false;
  }
  if (next.state === "activated") return next.base_version === next.published_version;
  return next.state === "unconfirmed" && next.base_version === draft.base_version && next.base_commit === draft.base_commit;
}

export function useManualAssetDraft(enabled: boolean) {
  const [draft, setDraft] = useState<ManualAssetDraft | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const requestId = useRef(0);
  const writing = useRef(false);
  const origin = useRef(selectedAgent());
  const active = useRef(false);
  const isCurrent = useCallback(() => active.current && selectedAgent() === origin.current, []);

  useLayoutEffect(() => {
    active.current = enabled;
    return () => {
      active.current = false;
      requestId.current += 1;
    };
  }, [enabled]);

  const refresh = useCallback(async () => {
    if (writing.current || !isCurrent()) return;
    const id = ++requestId.current;
    try {
      const next = await getManualAssetDraft();
      if (id !== requestId.current || !isCurrent()) return;
      setDraft(next);
      setError("");
    } catch (cause) {
      if (id === requestId.current && isCurrent()) {
        setError(cause instanceof Error ? cause.message : "读取手动草稿失败。");
      }
    }
  }, [isCurrent]);

  useEffect(() => {
    if (enabled) void refresh();
    else {
      setDraft(null);
      setError("");
    }
  }, [enabled, refresh]);

  const state = draft?.state;
  useEffect(() => {
    if (!enabled || state !== "unconfirmed") return;
    let timer: number;
    let stopped = false;
    const check = async () => {
      await refresh();
      if (!stopped) timer = window.setTimeout(check, 4000);
    };
    timer = window.setTimeout(check, 4000);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [enabled, state, refresh]);

  async function update(operation: (current: ManualAssetDraft) => Promise<ManualAssetDraft>) {
    if (!isCurrent()) throw new Error("已离开当前 Harness 资产页。");
    if (!draft || writing.current) throw new Error("草稿操作正在处理，请稍后重试。");
    const id = ++requestId.current;
    writing.current = true;
    setBusy(true);
    try {
      const next = await operation(draft);
      if (id === requestId.current && isCurrent()) {
        setDraft(next);
        setError("");
      }
    } catch (cause) {
      // 请求可能已在服务端完成。重新读取状态，避免把上线确认当成失败后重发。
      if (id === requestId.current && isCurrent() && !(cause instanceof DOMException && cause.name === "AbortError")) {
        try {
          const next = await getManualAssetDraft();
          if (id === requestId.current && isCurrent()) {
            setDraft(next);
            setError("");
            if (operation === releaseManualAssetDraft && observedRelease(draft, next)) return;
          }
        } catch { /* 保留最后一次成功读取的草稿。 */ }
      }
      throw cause;
    } finally {
      writing.current = false;
      setBusy(false);
    }
  }

  const save: SaveManualAsset = (change) => update((current) => saveManualAssetDraft(current, change));

  return {
    draft, error, busy, refresh, save,
    release: () => update(releaseManualAssetDraft),
    discard: () => update(discardManualAssetDraft),
  };
}

export function ManualDraftPanel({
  manual,
  selectedVersion,
  roleNames,
  onRefreshProduction,
}: {
  manual: ReturnType<typeof useManualAssetDraft>;
  selectedVersion: number;
  roleNames: Map<string, string>;
  onRefreshProduction: () => void;
}) {
  const { draft, error, busy, refresh, release, discard } = manual;
  const [reviewedRevision, setReviewedRevision] = useState<number | null>(null);
  const [discarding, setDiscarding] = useState(false);
  const [actionError, setActionError] = useState("");
  const changes = draft?.changes ?? [];
  const stale = draft?.state === "stale";
  const pending = draft?.state === "unconfirmed";
  const versionChanged = !!draft && !pending && draft.base_version !== selectedVersion;
  const canRelease = !!draft && draft.state === "editing" && !versionChanged && changes.length > 0;
  const reviewing = reviewedRevision !== null && changes.length > 0;

  const groups = new Map<string, ManualAssetChange[]>();
  for (const change of changes) {
    const group = groups.get(change.role) ?? [];
    group.push(change);
    groups.set(change.role, group);
  }

  async function confirm(operation: () => Promise<void>, fallback: string) {
    setActionError("");
    try {
      await operation();
      setReviewedRevision(null);
      setDiscarding(false);
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : fallback);
    }
  }

  return (
    <section className="manual-draft" aria-label="手动资产草稿">
      <div className="prompt-draft-bar">
        <div className="prompt-draft-status" role="status">
          {!draft && (error ? "手动草稿暂时不可用" : "正在读取手动草稿…")}
          {draft?.state === "editing" && (versionChanged
            ? "生产版本已更新，请刷新版本。"
            : changes.length > 0 ? `${changes.length} 项手动草稿待上线 · 基于 v${draft.base_version}` : "手动草稿 · 暂无变更")}
          {stale && "生产版本已变化，手动草稿已保留。"}
          {pending && `v${draft.published_version ?? ""} 等待执行端确认`}
          {draft?.state === "activated" && `v${draft.published_version ?? draft.base_version} 已上线`}
          {draft?.message && <p className="manual-draft-message">{draft.message}</p>}
        </div>
        {draft && changes.length > 0 && (
          <button type="button" className="prompt-draft-release" disabled={busy} onClick={() => {
            setActionError("");
            setDiscarding(false);
            setReviewedRevision(draft.revision);
          }}>{canRelease ? "预览上线变更" : "查看草稿变更"}</button>
        )}
        {pending && <button type="button" className="prompt-file-edit" disabled={busy} onClick={() => void refresh()}>核对上线状态</button>}
        {(stale || versionChanged) && <button type="button" className="prompt-file-edit" disabled={busy} onClick={onRefreshProduction}>刷新生产版本</button>}
        {!pending && error && <button type="button" className="prompt-file-edit" disabled={busy} onClick={() => void refresh()}>重试读取草稿</button>}
      </div>
      {error && <p role="alert" className="skill-load-error">{error}</p>}
      {actionError && !reviewing && <p role="alert" className="skill-load-error">{actionError}</p>}
      <Sheet open={reviewing} onOpenChange={(open) => !open && !busy && setReviewedRevision(null)}>
        <SheetContent side="right" aria-describedby={undefined}>
          <SheetHeader><SheetTitle>本套手动草稿变更</SheetTitle></SheetHeader>
          <div className="manual-draft-body">
            {canRelease && <p>确认后，以下变更将随同一个新版本上线。</p>}
            {[...groups].map(([role, entries]) => (
              <section className="manual-change-group" key={role}>
                <h3>{roleNames.get(role) ?? agentLabel(role)}</h3>
                {entries.map((entry, index) => (
                  <details className="manual-change" key={`${entry.operation}:${entry.name}:${index}`}>
                    <summary>{labels[entry.operation]} · {entry.name}</summary>
                    {entry.before !== null && <><p>原内容</p><pre className="prompt-full">{entry.before}</pre></>}
                    {entry.after !== null && <><p>草稿内容</p><pre className="prompt-full">{entry.after}</pre></>}
                  </details>
                ))}
              </section>
            ))}
            {reviewedRevision !== draft?.revision && canRelease && <p role="alert">草稿已更新，请关闭后重新预览。</p>}
            {discarding && <p>丢弃后将清除上面列出的草稿变更。</p>}
            {actionError && <p role="alert" className="skill-load-error">{actionError}</p>}
            <div className="prompt-edit-actions">
              {canRelease && (
                <button type="button" disabled={busy || reviewedRevision !== draft?.revision}
                  onClick={() => void confirm(release, "上线失败。")}>
                  {busy ? "上线请求处理中…" : "确认上线"}
                </button>
              )}
              {stale && (
                <button type="button" disabled={busy} onClick={() => {
                  if (discarding) void confirm(discard, "丢弃草稿失败。");
                  else setDiscarding(true);
                }}>{discarding ? "确认丢弃" : "丢弃过期草稿"}</button>
              )}
              <button type="button" disabled={busy} onClick={() => setReviewedRevision(null)}>关闭</button>
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </section>
  );
}
