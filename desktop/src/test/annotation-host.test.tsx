/**
 * AnnotationHost 交互测试（REQ-20261027-213246 FR-001＋2026-10-27 修订：
 * 内置标签一键挂上、自定义免标签名、点高亮查看/撤回删除）。
 *
 * jsdom 无 CSS Custom Highlight（applyHighlights 自动降级，不影响本测试）；
 * 划选走真实 window.getSelection + Range；点高亮用注入 caretRangeFromPoint。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AnnotationHost } from "@/components/workspace/annotations/AnnotationHost";
import * as api from "@/lib/api";
import { useAnnotationsStore } from "@/stores/annotations";
import { useWorkspaceStore } from "@/stores/workspace";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
  fetchAnnotations: vi.fn(async () => ({ annotations: [] })),
  createAnnotation: vi.fn(),
  updateAnnotation: vi.fn(),
  deleteAnnotation: vi.fn(),
  clearAnnotations: vi.fn(),
  dispatchAnnotations: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const mockedCreate = vi.mocked(api.createAnnotation);
const mockedUpdate = vi.mocked(api.updateAnnotation);
const mockedRemove = vi.mocked(api.deleteAnnotation);

function setupHost(props?: Partial<Parameters<typeof AnnotationHost>[0]>) {
  const container = document.createElement("div");
  container.innerHTML = "<p>第一段开头，加粗中间，第一段结尾。</p><p>第二段独立文字内容。</p>";
  document.body.appendChild(container);
  const view = render(
    <AnnotationHost
      rel="总纲.md"
      enabled
      markdown="unused-in-test"
      reanchorTarget={null}
      onReanchorDone={vi.fn()}
      {...props}
    >
      <div ref={(el) => { if (el) el.replaceChildren(...Array.from(container.childNodes)); }} />
    </AnnotationHost>,
  );
  const host = document.querySelector(".ann-host") as HTMLElement;
  const firstP = host.querySelectorAll("p")[0];
  return { view, host, firstP, secondP: host.querySelectorAll("p")[1] };
}

function selectRange(startNode: Node, start: number, endNode: Node, end: number) {
  const range = document.createRange();
  range.setStart(startNode.firstChild!, start);
  range.setEnd(endNode.firstChild!, end);
  const sel = window.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(range);
}

async function fireMouseUp(host: HTMLElement, opts?: MouseEventInit) {
  host.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, ...opts }));
  await new Promise((r) => setTimeout(r, 20));
}

beforeEach(() => {
  vi.clearAllMocks();
  useAnnotationsStore.setState({ workspaceId: "ws-1", annotations: [], interacting: false });
  useWorkspaceStore.setState({ activeWorkspaceId: "ws-1", hasApiKey: true });
});

afterEach(() => {
  document.body.innerHTML = "";
  document.getSelection()?.removeAllRanges();
  delete (document as unknown as { caretRangeFromPoint?: unknown }).caretRangeFromPoint;
  useAnnotationsStore.setState({ interacting: false });
});

describe("AnnotationHost（FR-001＋2026-10-27 修订）", () => {
  it("AC-002：划选过短提示扩选，不弹浮条、不建标注", async () => {
    const { toast } = await import("sonner");
    const { host, firstP } = setupHost();
    selectRange(firstP, 0, firstP, 3); // 「第一段」3 字 < 6
    await fireMouseUp(host);
    expect(toast.info).toHaveBeenCalledWith(expect.stringContaining("划选太短"));
    expect(document.querySelector(".ann-float-bar")).toBeNull();
    expect(mockedCreate).not.toHaveBeenCalled();
  });

  it("修订：点内置标签一键直接挂上（不弹意见卡、无 note）", async () => {
    const user = userEvent.setup();
    mockedCreate.mockResolvedValue({
      id: "a-1", artifact_rel: "总纲.md", label: "contradiction",
      quote: "头，加粗中间，", status: "pending",
    });
    const { host, firstP } = setupHost();
    selectRange(firstP, 4, firstP, 11); // 「头，加粗中间，」7 字 ≥ 6
    await fireMouseUp(host);
    await user.click(await screen.findByRole("button", { name: "前后矛盾" }));

    await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1));
    const arg = mockedCreate.mock.calls[0][1];
    expect(arg.label).toBe("contradiction");
    expect(arg.quote).toBe("头，加粗中间，");
    expect(arg.note).toBeUndefined(); // 一键挂上：无意见
    expect(arg.context_before).toContain("第一段");
    // 浮条收起、交互门解除
    expect(document.querySelector(".ann-float-bar")).toBeNull();
    expect(useAnnotationsStore.getState().interacting).toBe(false);
  });

  it("修订：自定义免标签名——意见卡只写意见，空意见拦、写了才发", async () => {
    const user = userEvent.setup();
    const { toast } = await import("sonner");
    mockedCreate.mockResolvedValue({
      id: "a-2", artifact_rel: "总纲.md", label: "custom",
      quote: "头，加粗中间，", status: "pending",
    });
    const { host, firstP } = setupHost();
    selectRange(firstP, 4, firstP, 11);
    await fireMouseUp(host);
    await user.click(await screen.findByRole("button", { name: "自定义" }));
    // 意见卡没有标签名输入框
    expect(screen.queryByPlaceholderText("标签名（必填）")).toBeNull();

    await user.click(screen.getByRole("button", { name: "挂上标注" }));
    expect(toast.info).toHaveBeenCalledWith("自定义标注要写一句意见");
    expect(mockedCreate).not.toHaveBeenCalled();

    await user.type(screen.getByPlaceholderText(/意见（必填）/), "这句设定太虚");
    await user.click(screen.getByRole("button", { name: "挂上标注" }));
    await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1));
    const arg = mockedCreate.mock.calls[0][1];
    expect(arg.label).toBe("custom");
    expect(arg.custom_label).toBeUndefined();
    expect(arg.note).toBe("这句设定太虚");
  });

  it("修订：点已挂标注的高亮 → 详情卡（标签/原文/意见）→ 撤回删除", async () => {
    const user = userEvent.setup();
    const { toast } = await import("sonner");
    const ann = {
      id: "a-3", artifact_rel: "总纲.md", label: "abstract",
      note: "法则之力太虚", quote: "头，加粗中间，", status: "pending",
      context_before: "", context_after: "",
    };
    useAnnotationsStore.setState({ annotations: [ann] });
    mockedRemove.mockResolvedValue({ status: "ok", deleted: "a-3" });
    const { host, firstP } = setupHost();

    // 注入 caretRangeFromPoint（jsdom 没有）：指向标注覆盖的文字内部
    const caret = document.createRange();
    caret.setStart(firstP.firstChild!, 6);
    caret.setEnd(firstP.firstChild!, 6);
    (document as unknown as { caretRangeFromPoint: unknown }).caretRangeFromPoint = () => caret;
    document.getSelection()?.removeAllRanges(); // 折叠点击（无划选）
    await fireMouseUp(host, { clientX: 10, clientY: 10 });

    expect(await screen.findByRole("dialog", { name: "标注详情" })).toBeInTheDocument();
    expect(screen.getByText("抽象设定")).toBeInTheDocument();
    expect(screen.getByText(/法则之力太虚/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "撤回删除" }));
    await waitFor(() => expect(mockedRemove).toHaveBeenCalledWith("ws-1", "a-3"));
    expect(toast.success).toHaveBeenCalledWith("已撤回该标注");
    // 详情卡随删除自动消失
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "标注详情" })).not.toBeInTheDocument(),
    );
  });

  it("FR-002：重框选模式下划选直接换锚点，不弹浮条", async () => {
    const onDone = vi.fn();
    mockedUpdate.mockResolvedValue({
      id: "a-9", artifact_rel: "总纲.md", label: "custom",
      quote: "第二段独立文字内", status: "pending",
    } as never);
    const { host, secondP } = setupHost({ reanchorTarget: "a-9", onReanchorDone: onDone });
    selectRange(secondP, 0, secondP, 8);
    await fireMouseUp(host);
    expect(document.querySelector(".ann-float-bar")).toBeNull();
    await waitFor(() => {
      expect(mockedUpdate).toHaveBeenCalledWith(
        "ws-1",
        "a-9",
        expect.objectContaining({
          reanchor: expect.objectContaining({ quote: "第二段独立文字内" }),
        }),
      );
    });
    expect(onDone).toHaveBeenCalled();
  });
});
