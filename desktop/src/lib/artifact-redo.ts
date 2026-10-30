/**
 * 删除重做客户端资格判定（REQ-20261030-211446 全量开放：六类产物）。
 *
 * 与服务端 artifact_redo.redo_eligibility 同口径（服务端是权威，这里只管
 * 按钮可见性/禁用提示）——从下往上不级联：
 * - 主题：总纲不存在才可删
 * - 总纲：卷纲/章纲/正文 全空才可删
 * - 卷纲第N卷：本卷「本卷章号范围」内无章纲；范围解析失败兜底章纲全空
 * - 章纲第N章：第N章正文不存在才可删
 * - 正文：无下游，随时可删
 * - 文风：特例随时可删（DEC-002：重写文风不等于重写正文）
 * 任意位置可删（DEC-003）：中间卷/章可删，不要求最末。
 */

const _VOLUME_RE = /^卷纲\/第(\d+)卷\.md$/;
const _CHAPTER_RE = /^章纲\/第(\d+)章\.md$/;
// 标题组排除分隔符（与服务端 contracts.TEXT_FILE_PATTERN 收紧同口径）
const _TEXT_RE = /^正文\/第(\d+)章[^/\\]*\.md$/;
const _VOLUME_RANGE_RE = /本卷章号范围\s*[:：]\s*(?:第\s*)?(\d+)\s*[-—–~～至]\s*(\d+)\s*章?/g;

export type RedoKind = "theme" | "outline" | "volume" | "chapter" | "style" | "text";
export type RedoEligibility = { eligible: boolean; reason: string };

export function redoTargetKind(rel: string): RedoKind | null {
  if (rel === "主题.md") return "theme";
  if (rel === "总纲.md") return "outline";
  if (rel === "文风.md") return "style";
  if (_VOLUME_RE.test(rel)) return "volume";
  if (_CHAPTER_RE.test(rel)) return "chapter";
  if (_TEXT_RE.test(rel)) return "text";
  return null;
}

function _numbers(rels: string[], re: RegExp): number[] {
  const out: number[] = [];
  for (const rel of rels) {
    const m = re.exec(rel);
    if (m) out.push(Number(m[1]));
  }
  return out;
}

/** 与 contracts.parse_volume_chapter_range 同口径：唯一一处且数值合法才有效。 */
function _volumeRange(markdown: string): [number, number] | null {
  const ranges = [...markdown.matchAll(_VOLUME_RANGE_RE)];
  if (ranges.length !== 1) return null;
  const start = Number(ranges[0][1]);
  const end = Number(ranges[0][2]);
  if (1 <= start && start <= end && end - start <= 1000) return [start, end];
  return null;
}

export function redoEligibility(rel: string, files: { rel: string; markdown: string }[]): RedoEligibility {
  const kind = redoTargetKind(rel);
  if (!kind) return { eligible: false, reason: "删除重做只支持五段产物（主题/总纲/卷纲/章纲/文风/正文）" };
  const rels = files.map((f) => f.rel);
  const chapters = _numbers(rels, _CHAPTER_RE);
  const texts = _numbers(rels, _TEXT_RE);

  if (kind === "theme") {
    if (rels.includes("总纲.md")) {
      return { eligible: false, reason: "主题不是最下游：总纲已存在，先删除重做总纲" };
    }
    return { eligible: true, reason: "" };
  }
  if (kind === "outline") {
    const downstream = [
      rels.some((r) => r.startsWith("卷纲/")) && "卷纲",
      chapters.length > 0 && "章纲",
      texts.length > 0 && "正文",
    ].filter(Boolean) as string[];
    if (downstream.length) {
      return { eligible: false, reason: `已有下游产物（${downstream.join("、")}），总纲不是最下游，先处理下游才能删除重做` };
    }
    return { eligible: true, reason: "" };
  }
  if (kind === "volume") {
    const markdown = files.find((f) => f.rel === rel)?.markdown ?? "";
    const range = _volumeRange(markdown);
    if (!range) {
      if (chapters.length) {
        return { eligible: false, reason: "无法解析该卷的「本卷章号范围」，且章纲目录非空；要求章纲全空才可删除该卷" };
      }
      return { eligible: true, reason: "" };
    }
    const hit = chapters.filter((c) => range[0] <= c && c <= range[1]);
    if (hit.length) {
      return { eligible: false, reason: `该卷范围内仍有章纲（第 ${Math.min(...hit)}-${Math.max(...hit)} 章），先删除重做这些章纲` };
    }
    return { eligible: true, reason: "" };
  }
  if (kind === "chapter") {
    const current = Number(_CHAPTER_RE.exec(rel)?.[1]);
    if (texts.includes(current)) {
      return { eligible: false, reason: `第 ${current} 章正文仍在，先删除重做该章正文` };
    }
    return { eligible: true, reason: "" };
  }
  // 正文（无下游）与文风（DEC-002 特例）：随时可删
  return { eligible: true, reason: "" };
}

/**
 * 服务端中文标签 → desktop 显示口径：contracts 的 LABEL_THEME 是「题材」，
 * desktop 全局口径是「主题」（pipeline-steps 同源），直显处统一走此映射。
 */
export function stageDisplayLabel(label: string): string {
  return label === "题材" ? "主题" : label;
}

/**
 * 编号空档检测（FR-004）：只报「区间内部」的洞（最小号..最大号之间缺号），
 * 不把尚未写到的尾部当缺失——顺序创作自然在末尾追加，不会误报；
 * 删中间章/卷留下的洞正好是内部洞（删哪补哪）。
 */
export function findArtifactGaps(files: { rel: string }[]): { outline: number[]; text: number[] } {
  const rels = files.map((f) => f.rel);
  const holes = (nums: number[]): number[] => {
    if (nums.length < 2) return [];
    const min = Math.min(...nums);
    const max = Math.max(...nums);
    const present = new Set(nums);
    const out: number[] = [];
    for (let n = min; n <= max; n++) if (!present.has(n)) out.push(n);
    return out;
  };
  return {
    outline: holes(_numbers(rels, _CHAPTER_RE)),
    text: holes(_numbers(rels, _TEXT_RE)),
  };
}
