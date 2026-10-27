/**
 * ManuscriptEditor 验收（REQ-20261018-115508 FR-009，AC-011 客户端半边；
 * T1 修复回归：resp.ok 检查、错误不弹成功、finalize 回填权威文本。
 * REQ-20261027-213246 AC-004：阅读/标注＋编辑双模式；定稿锁编辑不锁标注）。
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ManuscriptEditor } from "@/components/workspace/ManuscriptEditor";
import { apiFetch } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
  // 标注链路（AnnotationHost → annotations store）引用的同模块导出
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

const DRAFT = "# 第001章 · 开局\n> 状态：初稿｜2026-10-18\n\n正文内容";

function mockFetch(opts: { get?: unknown; put?: unknown; getOk?: boolean; putOk?: boolean }) {
  const resp = (body: unknown, ok: boolean) => ({
    ok,
    status: ok ? 200 : 500,
    json: async () => body,
  });
  vi.mocked(apiFetch).mockImplementation(((input: string, init?: RequestInit) => {
    if (init?.method === "PUT") return Promise.resolve(resp(opts.put, opts.putOk ?? true));
    return Promise.resolve(resp(opts.get, opts.getOk ?? true));
  }) as any);
}

async function enterEditing(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "编辑" }));
  return (await screen.findByRole("textbox", { name: "第 1 章正文编辑" })) as HTMLTextAreaElement;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ManuscriptEditor（FR-009 ＋ FR-003 双模式）", () => {
  it("AC-004：默认阅读/标注态（渲染文字可见、标注宿主在场），点「编辑」才进文本框", async () => {
    const user = userEvent.setup();
    mockFetch({ get: { chapter: 1, markdown: DRAFT, status: "draft", exists: true } });
    render(
      <ManuscriptEditor workspaceId="ws-1" chapter={1} rel="正文/第001章开局.md" />,
    );
    // 阅读态：渲染出的正文文字可见，且标注宿主包裹（可划词）
    expect(await screen.findByText("正文内容")).toBeInTheDocument();
    expect(document.querySelector(".ann-host.ann-enabled")).not.toBeNull();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();

    const textarea = await enterEditing(user);
    expect(textarea.value).toContain("正文内容");
    expect(screen.getByRole("button", { name: "人修定稿" })).toBeEnabled();
  });

  it("AC-004：人修定稿章锁编辑模式、不锁标注；初稿章可编辑", async () => {
    const user = userEvent.setup();
    const finalized = DRAFT.replace("初稿", "人修定稿");
    mockFetch({ get: { chapter: 1, markdown: finalized, status: "finalized", exists: true } });
    render(
      <ManuscriptEditor workspaceId="ws-1" chapter={1} rel="正文/第001章开局.md" />,
    );
    expect(await screen.findByText("已人修定稿")).toBeInTheDocument();
    // 标注宿主仍在（定稿不锁标注）
    expect(document.querySelector(".ann-host.ann-enabled")).not.toBeNull();
    // 编辑被锁
    expect(screen.getByRole("button", { name: "编辑" })).toBeDisabled();
    expect(screen.queryByRole("textbox", { name: "第 1 章正文编辑" })).not.toBeInTheDocument();
    expect(user).toBeTruthy();
  });

  it("未起草：显示占位不报错", async () => {
    mockFetch({ get: { chapter: 9, markdown: "", status: "draft", exists: false } });
    render(<ManuscriptEditor workspaceId="ws-1" chapter={9} />);
    expect(await screen.findByText("该章正文尚未起草。")).toBeInTheDocument();
  });

  it("保存成功（非 2xx 不弹成功提示——T1 回归）", async () => {
    const user = userEvent.setup();
    mockFetch({
      get: { chapter: 1, markdown: DRAFT, status: "draft", exists: true },
      put: { chapter: 1, markdown: DRAFT + "改", status: "draft", exists: true },
    });
    render(<ManuscriptEditor workspaceId="ws-1" chapter={1} />);
    await enterEditing(user);
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => {
      expect(apiFetch).toHaveBeenCalledWith(
        "/api/workspaces/ws-1/manuscript/1",
        expect.objectContaining({ method: "PUT" }),
      );
    });
  });

  it("PUT 500：不消费错误体、不弹成功、保留草稿（T1 resp.ok 回归）", async () => {
    const user = userEvent.setup();
    mockFetch({
      get: { chapter: 1, markdown: DRAFT, status: "draft", exists: true },
      put: { detail: "internal" },
      putOk: false,
    });
    const { toast } = await import("sonner");
    render(<ManuscriptEditor workspaceId="ws-1" chapter={1} />);
    const textarea = await enterEditing(user);
    await user.type(textarea, "增补");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("保存失败"));
    expect(toast.success).not.toHaveBeenCalled();
    expect(textarea.value).toContain("增补"); // 草稿保留
  });

  it("定稿成功：回填服务器权威文本（含状态行改写）、编辑禁用（C10 回归）", async () => {
    const user = userEvent.setup();
    const finalized = DRAFT.replace("初稿", "人修定稿");
    mockFetch({
      get: { chapter: 1, markdown: DRAFT, status: "draft", exists: true },
      put: { chapter: 1, markdown: finalized, status: "finalized", exists: true },
    });
    render(<ManuscriptEditor workspaceId="ws-1" chapter={1} />);
    const textarea = await enterEditing(user);
    await user.click(screen.getByRole("button", { name: "人修定稿" }));
    expect(await screen.findByText("已人修定稿")).toBeInTheDocument();
    expect(textarea.value).toContain("人修定稿"); // 权威文本回填
    expect(textarea).toBeDisabled();
  });

  it("GET 失败：错误态 + 重试，不误显示「尚未起草」（R6 回归）", async () => {
    mockFetch({ get: { detail: "x" }, getOk: false });
    render(<ManuscriptEditor workspaceId="ws-1" chapter={1} />);
    expect(await screen.findByText("正文读取失败。")).toBeInTheDocument();
    expect(screen.queryByText("该章正文尚未起草。")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });
});
