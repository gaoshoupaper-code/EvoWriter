/**
 * AnnotationHost 交互测试（REQ-20261027-213246 FR-001：AC-001 UI 半边＋AC-002）。
 *
 * jsdom 无 CSS Custom Highlight（applyHighlights 自动降级，不影响本测试）；
 * 划选走真实 window.getSelection + Range。锁定：
 *  - 划选过短：提示扩选，不出浮条、不建标注（AC-002）
 *  - 划选达标：标签浮条出现；点「前后矛盾」→ 意见卡；确认 → createAnnotation
 *    带 quote/上下文/意见（AC-001）
 *  - 自定义：不填标签名拦截提示；填了才发（DEC-008）
 *  - 重框选模式：划选直接 update reanchor，不弹浮条（FR-002）
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

async function fireMouseUp(host: HTMLElement) {
  host.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
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
  useAnnotationsStore.setState({ interacting: false });
});

describe("AnnotationHost（FR-001）", () => {
  it("AC-002：划选过短提示扩选，不弹浮条、不建标注", async () => {
    const { toast } = await import("sonner");
    const { host, firstP } = setupHost();
    selectRange(firstP, 0, firstP, 3); // 「第一段」3 字 < 6
    await fireMouseUp(host);
    expect(toast.info).toHaveBeenCalledWith(expect.stringContaining("划选太短"));
    expect(document.querySelector(".ann-float-bar")).toBeNull();
    expect(mockedCreate).not.toHaveBeenCalled();
  });

  it("AC-001：划选→浮条→意见卡→挂上标注（quote＋上下文＋意见）", async () => {
    const user = userEvent.setup();
    mockedCreate.mockResolvedValue({
      id: "a-1", artifact_rel: "总纲.md", label: "contradiction",
      quote: "头，加粗中间，", status: "pending",
    });
    const { host, firstP } = setupHost();
    selectRange(firstP, 4, firstP, 11); // 「头，加粗中间，」7 字 ≥ 6
    await fireMouseUp(host);
    // 浮条按钮（React 状态异步刷新，用 findBy）
    expect(await screen.findByRole("button", { name: "前后矛盾" })).toBeInTheDocument();
    expect(document.querySelector(".ann-float-bar")).not.toBeNull();

    await user.click(screen.getByRole("button", { name: "前后矛盾" }));
    const noteInput = await screen.findByPlaceholderText(/意见（选填）/);
    await user.type(noteInput, "和第二段对不上");
    await user.click(screen.getByRole("button", { name: "挂上标注" }));

    await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1));
    const arg = mockedCreate.mock.calls[0][1];
    expect(arg.artifact_rel).toBe("总纲.md");
    expect(arg.label).toBe("contradiction");
    expect(arg.quote).toBe("头，加粗中间，");
    expect(arg.context_before).toContain("第一段");
    expect(arg.context_after).toContain("第一段结尾");
    expect(arg.note).toBe("和第二段对不上");
    // 挂上后浮条收起、交互门解除（轮询恢复）
    expect(document.querySelector(".ann-float-bar")).toBeNull();
    expect(useAnnotationsStore.getState().interacting).toBe(false);
  });

  it("DEC-008：自定义不填标签名拦截，填了才发", async () => {
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
    await user.click(screen.getByRole("button", { name: "挂上标注" }));
    expect(toast.info).toHaveBeenCalledWith("自定义标签要先填标签名");
    expect(mockedCreate).not.toHaveBeenCalled();

    await user.type(screen.getByPlaceholderText("标签名（必填）"), "节奏拖沓");
    await user.click(screen.getByRole("button", { name: "挂上标注" }));
    await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1));
    expect(mockedCreate.mock.calls[0][1].custom_label).toBe("节奏拖沓");
  });

  it("FR-002：重框选模式下划选直接换锚点，不弹浮条", async () => {
    const onDone = vi.fn();
    mockedUpdate.mockResolvedValue({
      id: "a-9", artifact_rel: "总纲.md", label: "custom",
      quote: "第二段独立文字内容", status: "pending",
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
