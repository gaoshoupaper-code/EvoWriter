/**
 * 标注基础层测试（REQ-20261027-213246 FR-001/002/004 客户端基座）。
 *
 * 覆盖：DOM 文本索引（块级边界/逐字映射）、选区捕获（quote＋前后文）、
 * 锚点定位（唯一/多义消歧/找不到）、store 状态流转与轮询暂停门标志。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ANNOTATION_MIN_QUOTE,
  annotationRelAllowed,
  buildDomTextIndex,
  captureSelection,
  labelDisplay,
  locateAnnotation,
  normalizeWs,
} from "@/lib/annotations";
import { annotationsOfRel, useAnnotationsStore } from "@/stores/annotations";
import type { ArtifactAnnotation } from "@/lib/types";
import * as api from "@/lib/api";

vi.mock("@/lib/api", () => ({
  fetchAnnotations: vi.fn(),
  createAnnotation: vi.fn(),
  updateAnnotation: vi.fn(),
  deleteAnnotation: vi.fn(),
  clearAnnotations: vi.fn(),
  dispatchAnnotations: vi.fn(),
}));

const mockedFetch = vi.mocked(api.fetchAnnotations);
const mockedCreate = vi.mocked(api.createAnnotation);
const mockedRemove = vi.mocked(api.deleteAnnotation);
const mockedClear = vi.mocked(api.clearAnnotations);
const mockedDispatch = vi.mocked(api.dispatchAnnotations);

function makeAnn(patch: Partial<ArtifactAnnotation> = {}): ArtifactAnnotation {
  return {
    id: "a-1",
    artifact_rel: "总纲.md",
    label: "abstract",
    quote: "他一挥手",
    status: "pending",
    ...patch,
  };
}

// ── 工具常量 ───────────────────────────────────────────────


describe("标注元数据", () => {
  it("划选最短长度阈值导出为 6（FR-001 扩选提示依据）", () => {
    expect(ANNOTATION_MIN_QUOTE).toBe(6);
  });

  it("范围判定：五类产物可标注，主题不可（DEC-006）", () => {
    expect(annotationRelAllowed("总纲.md")).toBe(true);
    expect(annotationRelAllowed("文风.md")).toBe(true);
    expect(annotationRelAllowed("卷纲/第01卷.md")).toBe(true);
    expect(annotationRelAllowed("章纲/第001章.md")).toBe(true);
    expect(annotationRelAllowed("正文/第001章开局.md")).toBe(true);
    expect(annotationRelAllowed("主题.md")).toBe(false);
  });

  it("标签显示名：内置取映射，自定义拼标签名（DEC-008）", () => {
    expect(labelDisplay({ label: "abstract" })).toBe("抽象设定");
    expect(labelDisplay({ label: "contradiction" })).toBe("前后矛盾");
    expect(labelDisplay({ label: "custom", custom_label: "太虚了" })).toBe("自定义·太虚了");
    expect(labelDisplay({ label: "custom" })).toBe("自定义");
  });
});


// ── DOM 文本索引与选区 ────────────────────────────────────


describe("buildDomTextIndex / captureSelection", () => {
  function setup() {
    const container = document.createElement("div");
    container.innerHTML = `
      <h2>核心纲</h2>
      <p>第一段开头，<strong>加粗中间</strong>，第一段结尾。</p>
      <p>第二段独立文字。</p>
    `;
    document.body.appendChild(container);
    return container;
  }

  afterEach(() => {
    document.body.innerHTML = "";
    document.getSelection()?.removeAllRanges();
  });

  it("块级边界插入换行，map 逐字映射到文本节点", () => {
    // DOM API 构造（无模板缩进杂散空白），专测块级分隔符
    const container = document.createElement("div");
    const h = document.createElement("h2");
    h.textContent = "核心纲";
    const p1 = document.createElement("p");
    p1.textContent = "第一段结尾。";
    const p2 = document.createElement("p");
    p2.textContent = "第二段。";
    container.append(h, p1, p2);
    document.body.appendChild(container);

    const idx = buildDomTextIndex(container);
    expect(idx.text).toBe("核心纲\n第一段结尾。\n第二段。");
    // 分隔符位为 null，文字位都有映射
    expect(idx.map[idx.text.indexOf("核")]).not.toBeNull();
    expect(idx.map[idx.text.indexOf("\n")]).toBeNull();
    container.remove();
  });

  it("捕获跨 strong 的选区：quote 为所见文字，前后文窗口齐全", () => {
    const container = setup();
    const p = container.querySelectorAll("p")[0];
    const strong = p.querySelector("strong")!;
    const range = document.createRange();
    range.setStart(p.firstChild!, 5); // 「第一段开头」的逗号
    range.setEnd(strong.firstChild!, 1); // 「加」末
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);

    const capture = captureSelection(container);
    expect(capture).not.toBeNull();
    expect(capture!.quote).toBe("，加");
    expect(capture!.contextBefore).toContain("第一段开头");
    expect(capture!.contextAfter).toContain("粗中间");
    container.remove();
  });

  it("容器外选区返回 null", () => {
    const container = setup();
    const outside = document.createElement("p");
    outside.textContent = "外部文字";
    document.body.appendChild(outside);
    const range = document.createRange();
    range.selectNodeContents(outside);
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    expect(captureSelection(container)).toBeNull();
    container.remove();
    outside.remove();
  });
});

// ── 归一与锚点定位 ────────────────────────────────────────


describe("normalizeWs / locateAnnotation", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("空白折叠并保留原索引映射", () => {
    const { out, map } = normalizeWs("  a \n\n b  ");
    expect(out).toBe("a b");
    expect(map).toHaveLength(3);
    expect(map[0]).toBe(2); // 'a'
    expect(map[2]).toBe(7); // 'b'
  });

  it("唯一命中返回该位置 Range", () => {
    const container = document.createElement("div");
    container.innerHTML = "<p>唯一句子在这里。</p><p>别的段落。</p>";
    document.body.appendChild(container);
    const idx = buildDomTextIndex(container);
    const range = locateAnnotation(idx, { quote: "唯一句子" });
    expect(range).not.toBeNull();
    expect(range!.toString()).toBe("唯一句子");
    container.remove();
  });

  it("多次命中用前后文消歧（DEC-010 锚定上下文窗口）", () => {
    const container = document.createElement("div");
    container.innerHTML = "<p>第一处重复词后面有甲。</p><p>中间句。</p><p>第二处重复词后面有乙。</p>";
    document.body.appendChild(container);
    const idx = buildDomTextIndex(container);
    const range = locateAnnotation(idx, {
      quote: "重复词",
      context_before: "第二处",
      context_after: "后面有乙",
    });
    expect(range).not.toBeNull();
    expect(range!.startContainer.textContent).toContain("第二处");
    container.remove();
  });

  it("找不到返回 null（已失效标注不高亮）", () => {
    const container = document.createElement("div");
    container.innerHTML = "<p>全新内容。</p>";
    document.body.appendChild(container);
    const idx = buildDomTextIndex(container);
    expect(locateAnnotation(idx, { quote: "早没了的原句" })).toBeNull();
    container.remove();
  });
});


// ── store ────────────────────────────────────────────────


describe("useAnnotationsStore", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAnnotationsStore.setState({ workspaceId: null, annotations: [], interacting: false });
  });

  it("load 写入标注；切工作区清空旧数据（竞态保护）", async () => {
    mockedFetch.mockResolvedValue({ annotations: [makeAnn()] });
    await useAnnotationsStore.getState().load("ws1");
    expect(useAnnotationsStore.getState().annotations).toHaveLength(1);

    mockedFetch.mockResolvedValue({ annotations: [] });
    await useAnnotationsStore.getState().load("ws2");
    expect(useAnnotationsStore.getState().workspaceId).toBe("ws2");
    expect(useAnnotationsStore.getState().annotations).toHaveLength(0);
  });

  it("create/remove/clear 更新本地列表", async () => {
    useAnnotationsStore.setState({ workspaceId: "ws1", annotations: [makeAnn()] });
    mockedCreate.mockResolvedValue(makeAnn({ id: "a-2", artifact_rel: "文风.md" }));
    await useAnnotationsStore.getState().create({
      artifact_rel: "文风.md", label: "contradiction", quote: "短句快节奏",
    });
    expect(useAnnotationsStore.getState().annotations).toHaveLength(2);

    mockedRemove.mockResolvedValue({ status: "ok", deleted: "a-1" });
    await useAnnotationsStore.getState().remove("a-1");
    expect(useAnnotationsStore.getState().annotations.map((a) => a.id)).toEqual(["a-2"]);

    mockedClear.mockResolvedValue({ status: "ok", deleted: 1 });
    useAnnotationsStore.setState({
      annotations: [
        makeAnn({ id: "p1", status: "pending" }),
        makeAnn({ id: "r1", status: "resolved" }),
      ],
    });
    const deleted = await useAnnotationsStore.getState().clear(["resolved"]);
    expect(deleted).toBe(1);
    expect(useAnnotationsStore.getState().annotations.map((a) => a.id)).toEqual(["p1"]);
  });

  it("dispatch 返回任务消息（界面负责走聊天通道）；interacting 标志驱动轮询暂停门", async () => {
    useAnnotationsStore.setState({ workspaceId: "ws1" });
    mockedDispatch.mockResolvedValue({
      message: "【标注修订任务】共 1 条…",
      annotation_ids: ["a-1"],
      invalidated: 0,
    });
    const result = await useAnnotationsStore.getState().dispatch();
    expect(result.annotation_ids).toEqual(["a-1"]);

    useAnnotationsStore.getState().setInteracting(true);
    expect(useAnnotationsStore.getState().interacting).toBe(true);
  });

  it("annotationsOfRel 按产物过滤", () => {
    useAnnotationsStore.setState({
      annotations: [makeAnn(), makeAnn({ id: "a-2", artifact_rel: "文风.md" })],
    });
    expect(annotationsOfRel(useAnnotationsStore.getState(), "文风.md").map((a) => a.id)).toEqual(["a-2"]);
  });
});
