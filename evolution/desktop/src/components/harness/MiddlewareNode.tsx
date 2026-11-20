import type { HarnessElementView, ProcessorChange } from "@/lib/api";
import { Sheet, SheetClose, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

export function MiddlewareNode({
  mw,
  change,
  activeHook,
}: {
  mw: Omit<HarnessElementView["middlewares"][number], "hook">;
  change?: ProcessorChange;
  activeHook: string;
}) {
  const detail = mw.hook_details?.[activeHook];
  const action = detail?.action || (mw.hooks?.length === 1 ? mw.description?.split("\n")[0] : null);
  const rowDescription = action || "用途资料不足。";
  const diffClass = change?.change_type === "added" ? "diff-add"
    : change?.change_type === "modified" ? "diff-mod" : "";

  return (
    <Sheet>
      <SheetTrigger asChild>
        <button type="button" className={`mw-node ${diffClass}`}>
          <code className="mw-node-class" title={mw.class_name}>{mw.class_name}</code>
          <span className="mw-node-desc" title={rowDescription}>{rowDescription}</span>
        </button>
      </SheetTrigger>

      <SheetContent side="right" aria-describedby={undefined}>
        <SheetHeader>
          <SheetTitle title={mw.class_name}>{mw.class_name}</SheetTitle>
          <SheetClose className="mw-sheet-close">关闭详情</SheetClose>
        </SheetHeader>

        <div className="mw-sheet-body">
          <dl className="mw-sheet-meta">
            <div className="mw-detail-row">
              <dt className="mw-detail-label">当前钩子</dt>
              <dd className="mw-detail-val">{activeHook}</dd>
            </div>
            {mw.rule_version && (
              <div className="mw-detail-row">
                <dt className="mw-detail-label">规则版本</dt>
                <dd className="mw-detail-val" title={mw.rule_version}>{mw.rule_version.slice(0, 12)}</dd>
              </div>
            )}
          </dl>

          <dl className="mw-sheet-function">
            <div><dt>触发条件</dt><dd>{detail?.trigger || "资料不足。"}</dd></div>
            <div><dt>执行动作</dt><dd>{rowDescription}</dd></div>
            <div><dt>影响</dt><dd>{detail?.effect || "资料不足。"}</dd></div>
          </dl>

          {mw.description && mw.description !== action && (
            <div className="mw-sheet-section">
              <h3 className="mw-sheet-section-title">规则说明</h3>
              <pre className="mw-sheet-desc">{mw.description}</pre>
            </div>
          )}

          <div className="mw-sheet-section">
            <h3 className="mw-sheet-section-title">参数</h3>
            {change?.change_type === "modified" ? (
              <>
                <div className="mw-detail-row">
                  <span className="mw-detail-label">旧参数</span>
                  <span className="mw-detail-val diff-del">{JSON.stringify(change.params_change.old ?? {}, null, 2)}</span>
                </div>
                <div className="mw-detail-row">
                  <span className="mw-detail-label">新参数</span>
                  <span className="mw-detail-val diff-add">{JSON.stringify(change.params_change.new ?? {}, null, 2)}</span>
                </div>
              </>
            ) : Object.keys(mw.params ?? {}).length > 0 ? (
              <div className="mw-detail-val">{JSON.stringify(mw.params, null, 2)}</div>
            ) : (
              <p className="mw-sheet-empty">无参数。</p>
            )}
          </div>

          {change?.change_type === "modified" && change.class_change.old !== change.class_change.new && (
            <div className="mw-sheet-section">
              <h3 className="mw-sheet-section-title">类变更</h3>
              <div className="mw-detail-row">
                <span className="mw-detail-val diff-del">{change.class_change.old || "—"}</span>
                {" → "}
                <span className="mw-detail-val diff-add">{change.class_change.new || "—"}</span>
              </div>
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
