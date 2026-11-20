import { useState } from "react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";

export function AssetEditSheet({
  title,
  content,
  onSave,
  onClose,
  disabled,
}: {
  title: string;
  content: string;
  onSave: (content: string) => Promise<void>;
  onClose: () => void;
  disabled: boolean;
}) {
  const [body, setBody] = useState(content);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    setSaving(true);
    setError("");
    try {
      await onSave(body);
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存草稿失败。");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Sheet open onOpenChange={(open) => !open && !saving && onClose()}>
      <SheetContent side="right" aria-describedby={undefined}>
        <SheetHeader><SheetTitle>编辑草稿 · {title}</SheetTitle></SheetHeader>
        <div className="prompt-edit-body manual-asset-editor">
          <textarea
            aria-label={`${title}内容`}
            className="prompt-textarea"
            value={body}
            onChange={(event) => setBody(event.target.value)}
            spellCheck={false}
            disabled={saving}
          />
          {error && <p className="skill-load-error" role="alert">{error}</p>}
          <div className="prompt-edit-actions">
            <button type="button" disabled={disabled || saving || body === content} onClick={save}>
              {saving ? "保存中…" : "保存草稿"}
            </button>
            <button type="button" onClick={onClose} disabled={saving}>取消</button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
