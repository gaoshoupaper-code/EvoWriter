/**
 * 产物划词标注——渲染 DOM 文本索引与选区工具（REQ-20261027-213246 FR-001）。
 *
 * 产物渲染成 markdown 后没有源文件语法（**、表格线），用户划选的是「所见
 * 文字」。本模块把渲染容器序列化成带逐字映射的文本索引（块级元素边界插
 * \n，对齐服务端 normalize 的空白折叠），选区捕获（quote＋前后文窗口）、
 * 标注定位（重新渲染后找回锚点）共用同一索引，保证三处口径一致。
 */

/** 划选过短阈值（FR-001 失败语义：不足以唯一定位时提示扩选）。 */
export const ANNOTATION_MIN_QUOTE = 6;

/** 选区前后文窗口长度（字符）——发送给服务端做归一消歧。 */
export const ANNOTATION_CONTEXT_LEN = 40;

export type AnnotationLabel = "abstract" | "contradiction" | "custom";

/** 内置标签集（DEC-008：三个；「抽象设定」居首）。与服务端 LABEL_DISPLAY 对齐。 */
export const LABEL_OPTIONS: { value: AnnotationLabel; display: string }[] = [
  { value: "abstract", display: "抽象设定" },
  { value: "contradiction", display: "前后矛盾" },
  { value: "custom", display: "自定义" },
];

export function labelDisplay(ann: { label: string; custom_label?: string | null }): string {
  if (ann.label === "custom") return `自定义·${ann.custom_label || ""}`.replace(/·$/, "");
  return LABEL_OPTIONS.find((o) => o.value === ann.label)?.display ?? ann.label;
}

/** 标注范围（DEC-006）：总纲/文风/卷纲/章纲/正文五类；主题不做。 */
export function annotationRelAllowed(rel: string): boolean {
  return rel === "总纲.md" || rel === "文风.md" ||
    rel.startsWith("卷纲/") || rel.startsWith("章纲/") || rel.startsWith("正文/");
}

/** 块级元素边界（序列化时插入 \n；TD/TH 也算——对齐表格行的归一口径）。 */
const _BLOCK_TAGS = new Set([
  "P", "DIV", "SECTION", "ARTICLE", "UL", "OL", "LI", "DL", "DT", "DD",
  "H1", "H2", "H3", "H4", "H5", "H6", "BLOCKQUOTE", "PRE", "TABLE",
  "THEAD", "TBODY", "TFOOT", "TR", "TD", "TH", "FIGURE", "FIGCAPTION",
  "HR", "BR",
]);

export type DomPoint = { node: Text; offset: number } | null;

export type DomTextIndex = {
  /** 序列化文本（块级边界以 \n 分隔，贴近渲染所见） */
  text: string;
  /** 逐字映射：map[i] = 序列化第 i 个字符所在的文本节点与节点内偏移；分隔符位为 null */
  map: DomPoint[];
  /** markRange 的起止序列化位置（exclusive end）；无标记范围时为 null */
  rangeStart: number | null;
  rangeEnd: number | null;
};

/** 把选区端点解析到文本节点上（端点可能落在元素节点/容器本身）。 */
function resolvePoint(
  root: HTMLElement,
  node: Node,
  offset: number,
  forward: boolean,
): DomPoint {
  if (node.nodeType === Node.TEXT_NODE) return { node: node as Text, offset };
  // 元素节点：offset 是子节点序号——向后（正向）取该子树首个文本节点，
  // 向前（反向）取前一个子树的末个文本节点。
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let anchor: Text | null = null;
  let cur: Node | null;
  while ((cur = walker.nextNode())) {
    if (cur === node) { anchor = cur as Text; break; }
  }
  if (node.nodeType === Node.ELEMENT_NODE) {
    const el = node as Element;
    const child = forward ? el.childNodes[offset] : el.childNodes[Math.max(0, offset - 1)];
    if (child) {
      const sub = document.createTreeWalker(child, NodeFilter.SHOW_TEXT);
      const first = sub.nextNode() as Text | null;
      const last = ((): Text | null => {
        let p: Text | null = null;
        let n: Node | null;
        while ((n = sub.nextNode())) p = n as Text;
        return p;
      })();
      const picked = forward ? first : (last ?? first);
      if (picked) return { node: picked, offset: forward ? 0 : (picked.textContent?.length ?? 0) };
    }
  }
  if (anchor) return { node: anchor, offset: forward ? 0 : (anchor.textContent?.length ?? 0) };
  return null;
}

/**
 * 构建容器文本索引；传入 markRange 时记录其起止序列化位置（选区捕获用）。
 * 单次深度遍历：文本节点逐字登记，块级元素前置 \n（分隔位 map=null）。
 */
export function buildDomTextIndex(
  container: HTMLElement,
  markRange?: Range | null,
): DomTextIndex {
  let text = "";
  const map: DomPoint[] = [];
  let rangeStart: number | null = null;
  let rangeEnd: number | null = null;

  const startPt = markRange
    ? resolvePoint(container, markRange.startContainer, markRange.startOffset, true)
    : null;
  const endPt = markRange
    ? resolvePoint(container, markRange.endContainer, markRange.endOffset, false)
    : null;

  const visit = (node: Node): void => {
    if (node.nodeType === Node.TEXT_NODE) {
      const tn = node as Text;
      const data = tn.textContent ?? "";
      for (let i = 0; i < data.length; i++) {
        if (markRange && startPt && tn === startPt.node && i === startPt.offset) {
          rangeStart = text.length;
        }
        if (markRange && endPt && tn === endPt.node && i === endPt.offset) {
          rangeEnd = text.length;
        }
        map.push({ node: tn, offset: i });
        text += data[i];
      }
      // 端点落在节点末尾之后（offset === length）：登记为本节点末位
      if (markRange && startPt && tn === startPt.node && startPt.offset >= data.length) {
        rangeStart = text.length;
      }
      if (markRange && endPt && tn === endPt.node && endPt.offset >= data.length) {
        rangeEnd = text.length;
      }
      return;
    }
    if (node.nodeType === Node.ELEMENT_NODE) {
      const tag = (node as Element).tagName;
      // 首个块前不插分隔符（避免前导 \n）；嵌套块的重复 \n 由归一折叠
      if (_BLOCK_TAGS.has(tag) && text.length > 0) {
        map.push(null);
        text += "\n";
      }
      let child = node.firstChild;
      while (child) {
        visit(child);
        child = child.nextSibling;
      }
    }
  };

  let n: Node | null = container.firstChild;
  while (n) {
    visit(n);
    n = n.nextSibling;
  }
  // 选区终点落在末尾节点之后：兜底为文本末位
  if (markRange && rangeStart !== null && rangeEnd === null) rangeEnd = text.length;
  return { text, map, rangeStart, rangeEnd };
}

export type SelectionCapture = {
  quote: string;
  contextBefore: string;
  contextAfter: string;
};

/** 捕获容器内的当前选区 → quote＋前后文窗口（FR-001 输入）。 */
export function captureSelection(
  container: HTMLElement,
  ctxLen: number = ANNOTATION_CONTEXT_LEN,
): SelectionCapture | null {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;
  const range = sel.getRangeAt(0);
  if (!container.contains(range.commonAncestorContainer)) return null;
  const idx = buildDomTextIndex(container, range);
  if (idx.rangeStart === null || idx.rangeEnd === null || idx.rangeEnd <= idx.rangeStart) {
    return null;
  }
  const quote = idx.text.slice(idx.rangeStart, idx.rangeEnd);
  if (!quote.trim()) return null;
  return {
    quote,
    contextBefore: idx.text.slice(Math.max(0, idx.rangeStart - ctxLen), idx.rangeStart),
    contextAfter: idx.text.slice(idx.rangeEnd, idx.rangeEnd + ctxLen),
  };
}

/** 空白折叠归一（客户端只对渲染文本，无 markdown 语法），保留原索引映射。 */
export function normalizeWs(text: string): { out: string; map: number[] } {
  let out = "";
  const map: number[] = [];
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (/\s/.test(ch)) {
      const start = i;
      while (i < text.length && /\s/.test(text[i])) i++;
      if (out.length > 0 && i < text.length) {
        out += " ";
        map.push(start);
      }
      continue;
    }
    out += ch;
    map.push(i);
    i++;
  }
  return { out, map };
}

/** 序列化区间 → DOM Range（跳过分隔符位，找不到有效端点返回 null）。 */
function mapToRange(idx: DomTextIndex, start: number, end: number): Range | null {
  if (end <= start) return null;
  let s = Math.max(0, start);
  let e = Math.min(idx.map.length, end);
  while (s < e && !idx.map[s]) s++;
  while (e > s && !idx.map[e - 1]) e--;
  if (s >= e || !idx.map[s] || !idx.map[e - 1]) return null;
  const sp = idx.map[s]!;
  const ep = idx.map[e - 1]!;
  const range = document.createRange();
  range.setStart(sp.node, sp.offset);
  range.setEnd(ep.node, ep.offset + 1);
  return range;
}

export type AnchoredAnnotation = {
  quote: string;
  context_before?: string;
  context_after?: string;
};

/** 在当前渲染中定位标注锚点（重渲染后找回高亮位置）；找不到返回 null。 */
export function locateAnnotation(idx: DomTextIndex, ann: AnchoredAnnotation): Range | null {
  const nText = normalizeWs(idx.text);
  const nQuote = normalizeWs(ann.quote).out;
  if (!nQuote) return null;
  const candidates: number[] = [];
  let pos = 0;
  while ((pos = nText.out.indexOf(nQuote, pos)) !== -1) {
    candidates.push(pos);
    pos += 1;
  }
  if (candidates.length === 0) return null;
  let picked = candidates[0];
  if (candidates.length > 1 && (ann.context_before || ann.context_after)) {
    const nBefore = normalizeWs(ann.context_before ?? "").out;
    const nAll = normalizeWs(`${ann.context_before ?? ""}${ann.quote}${ann.context_after ?? ""}`).out;
    const at = nText.out.indexOf(nAll);
    if (at >= 0 && at + nBefore.length <= nText.out.length) {
      const derived = at + nBefore.length;
      if (candidates.includes(derived)) picked = derived;
    }
  }
  const sStart = nText.map[picked];
  const sEnd = nText.map[picked + nQuote.length - 1];
  if (sStart === undefined || sEnd === undefined) return null;
  return mapToRange(idx, sStart, sEnd + 1);
}

const HIGHLIGHT_KEYS = ["ann-pending", "ann-resolved", "ann-invalid"] as const;

/** 按状态给当前容器的标注挂 CSS Custom Highlight（WebView2/Chromium）。 */
export function applyHighlights(
  container: HTMLElement,
  annotations: { id: string; status: string; quote: string; context_before?: string; context_after?: string }[],
): void {
  const css = CSS as unknown as { highlights?: Map<string, unknown> };
  if (!("highlights" in CSS) || typeof (globalThis as { Highlight?: unknown }).Highlight !== "function") {
    return; // 不支持的环境降级：无高亮，不报错
  }
  const HighlightCtor = (globalThis as unknown as { Highlight: new (...ranges: Range[]) => unknown }).Highlight;
  const idx = buildDomTextIndex(container);
  const groups: Record<string, Range[]> = { pending: [], resolved: [], invalid: [] };
  for (const ann of annotations) {
    const range = locateAnnotation(idx, ann);
    if (range && groups[ann.status]) groups[ann.status].push(range);
  }
  for (const key of HIGHLIGHT_KEYS) {
    const status = key.replace("ann-", "");
    if (groups[status]?.length) {
      css.highlights!.set(key, new HighlightCtor(...groups[status]));
    } else {
      css.highlights!.delete(key);
    }
  }
}

/** 清掉本容器相关的所有高亮登记（卸载时调用）。 */
export function clearHighlights(): void {
  const css = CSS as unknown as { highlights?: Map<string, unknown> };
  if (!("highlights" in CSS)) return;
  for (const key of HIGHLIGHT_KEYS) css.highlights!.delete(key);
}
