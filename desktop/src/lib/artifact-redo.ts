/**
 * 定稿产物删除重做——客户端资格判定（REQ-20261027-213246 修订 2026-10-28）。
 *
 * 与服务端 artifact_redo.redo_eligibility 同口径（服务端是权威，这里只管
 * 按钮可见性/禁用提示）：
 * - 总纲：卷纲/章纲/正文 全空才可删
 * - 卷纲第N卷：N 为最大卷号且 章纲/正文 全空
 */

const _VOLUME_RE = /^卷纲\/第(\d+)卷\.md$/;

export type RedoEligibility = { eligible: boolean; reason: string };

export function redoTargetKind(rel: string): "outline" | "volume" | null {
  if (rel === "总纲.md") return "outline";
  if (_VOLUME_RE.test(rel)) return "volume";
  return null;
}

export function redoEligibility(rel: string, files: { rel: string }[]): RedoEligibility {
  const kind = redoTargetKind(rel);
  if (!kind) return { eligible: false, reason: "删除重做只支持总纲与卷纲" };
  const rels = files.map((f) => f.rel);
  const hasVolumes = rels.some((r) => r.startsWith("卷纲/"));
  const hasChapters = rels.some((r) => r.startsWith("章纲/"));
  const hasTexts = rels.some((r) => r.startsWith("正文/"));
  if (kind === "outline") {
    const downstream = [
      hasVolumes && "卷纲", hasChapters && "章纲", hasTexts && "正文",
    ].filter(Boolean) as string[];
    if (downstream.length) {
      return { eligible: false, reason: `已有下游产物（${downstream.join("、")}），总纲不是最下游，先处理下游才能删除重做` };
    }
    return { eligible: true, reason: "" };
  }
  const vols = rels
    .map((r) => _VOLUME_RE.exec(r)?.[1])
    .filter(Boolean)
    .map(Number);
  const current = Number(_VOLUME_RE.exec(rel)?.[1]);
  if (vols.length && current !== Math.max(...vols)) {
    return { eligible: false, reason: `第 ${current} 卷不是最下游（最新是第 ${Math.max(...vols)} 卷），只能删除重做最新一卷` };
  }
  const downstream = [hasChapters && "章纲", hasTexts && "正文"].filter(Boolean) as string[];
  if (downstream.length) {
    return { eligible: false, reason: `已有下游产物（${downstream.join("、")}），该卷不是最下游，先处理下游才能删除重做` };
  }
  return { eligible: true, reason: "" };
}
