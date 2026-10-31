/**
 * 会话排队与断连补跑集成测试（REQ-20261019-222403 FR-006/DEC-009，AC-006 客户端侧）。
 *
 * 场景：
 *   1. 生成中发送第二条消息 → assistant 行进入「排队中」（status queued 帧），
 *      前一条完成后收到 started/final → completed（排队串行的端到端界面契约）。
 *   2. 排队流断连 → 行落 failed + 待补跑标记；下一条消息提交时自动按原
 *      message_id 补发（服务端台账幂等入队），补跑完成后再发新消息。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

const encoder = new TextEncoder();
function sseEvent(type: string, data: unknown): string {
  return `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
}
function encode(events: string[]): Uint8Array[] {
  return events.map((e) => encoder.encode(e));
}

type Chunk = { done: false; value: Uint8Array } | { done: true; value: undefined };

/** 可控流：先吐 prefix，再在闸门上挂起，放行后吐 after 再结束。 */
function gatedStream(prefix: Chunk_source, after: Chunk_source) {
  let phase: "prefix" | "gate" | "after" = "prefix";
  let index = 0;
  let pending: ((c: Chunk) => void) | null = null;
  const serve = (chunks: Uint8Array[]): Chunk => {
    if (index < chunks.length) return { done: false, value: chunks[index++] };
    return { done: true, value: undefined };
  };
  return {
    read: () =>
      new Promise<Chunk>((resolve) => {
        if (phase === "prefix") {
          resolve(serve(prefix));
          if (index >= prefix.length) {
            // after 为空 = 吐完即结束（断流模拟）；否则挂闸门等放行
            phase = after.length ? "gate" : "after";
            index = 0;
          }
          return;
        }
        if (phase === "after") {
          resolve(serve(after));
          return;
        }
        pending = resolve; // gate：等放行
      }),
    cancel: () => {
      pending?.({ done: true, value: undefined });
      return Promise.resolve();
    },
    release: () => {
      if (phase === "gate") {
        phase = "after";
        index = 0;
        pending?.(serve(after));
        pending = null;
      }
    },
  };
}
type Chunk_source = Uint8Array[];

const RUN1_FINAL = encode([
  sseEvent("trace_event", {
    trace_id: "trace-1", event_id: "e1", sequence: 1, type: "run_start", status: "running",
    timestamp: "2026-01-01T00:00:00Z", source: "system",
    input: { workspace_id: "ws-1", thread_id: "thread-1", session_name: "s", endpoint: "screenplay.generate.stream" },
  }),
]);
const RUN1_AFTER = encode([
  sseEvent("model_stream", { content: "第一轮完成" }),
  sseEvent("final", {
    mode: "screenplay", thread_id: "thread-1", workspace_id: "ws-1", session_name: "s",
    workspace_path: "/test", title: "T", content: "第一轮完成", logline: "", synopsis: "",
    beats: [],
  }),
]);
const RUN2_PREFIX = encode([sseEvent("status", { status: "queued", message_id: "u-2" })]);
const RUN2_AFTER = encode([
  sseEvent("status", { status: "started", trace_id: "trace-2" }),
  sseEvent("model_stream", { content: "第二轮完成" }),
  sseEvent("final", {
    mode: "screenplay", thread_id: "thread-1", workspace_id: "ws-1", session_name: "s",
    workspace_path: "/test", title: "T", content: "第二轮完成", logline: "", synopsis: "",
    beats: [],
  }),
]);
const FINAL_ONLY = (text: string) => encode([
  sseEvent("model_stream", { content: text }),
  sseEvent("final", {
    mode: "screenplay", thread_id: "thread-1", workspace_id: "ws-1", session_name: "s",
    workspace_path: "/test", title: "T", content: text, logline: "", synopsis: "",
    beats: [],
  }),
]);

/** 排队后挂住、手动 break() 才断流的流（无 final → 断连兜底）。 */
function queuedThenBreakStream() {
  const prefix = RUN2_PREFIX;
  let phase: "prefix" | "gate" = "prefix";
  let index = 0;
  let pending: ((c: { done: false; value: Uint8Array } | { done: true; value: undefined }) => void) | null = null;
  return {
    read: () =>
      new Promise<{ done: false; value: Uint8Array } | { done: true; value: undefined }>((resolve) => {
        if (phase === "prefix") {
          resolve({ done: false, value: prefix[index++] });
          if (index >= prefix.length) phase = "gate";
          return;
        }
        pending = resolve;
      }),
    cancel: () => {
      pending?.({ done: true, value: undefined });
      return Promise.resolve();
    },
    breakStream: () => {
      pending?.({ done: true, value: undefined });
    },
    release: () => {}, // 类型兼容 gatedStream 工厂签名（本流用 breakStream 断流）
  };
}

// 按调用序消费的流工厂队列（每项返回带 release 的可控流）
let factories: Array<() => ReturnType<typeof gatedStream>> = [];
let streamCalls: { body: Record<string, unknown> }[] = [];

vi.mock("@/lib/stream", () => ({
  streamRequest: vi.fn().mockImplementation((_url: string, init: RequestInit) => {
    streamCalls.push({
      body: typeof init.body === "string"
        ? JSON.parse(init.body)
        : (init.body as unknown as Record<string, unknown>),
    });
    const factory = factories.shift();
    return Promise.resolve(factory ? factory() : gatedStream(FINAL_ONLY("兜底"), []));
  }),
}));

vi.mock("@/lib/api", () => ({
  API_BASE_URL: "",
  fetchMeOrNull: vi.fn().mockResolvedValue({ user_id: "u1", username: "test", is_admin: false, has_api_key: true }),
  fetchInit: vi.fn().mockResolvedValue({
    workspaces: [{ workspace_id: "ws-1", title: "T", domain: "writing", workspace_path: "/t",
      created_at: "", updated_at: "", session_count: 1, active_style_id: null }],
    styles: [],
  }),
  fetchWorkspaceBootstrap: vi.fn().mockResolvedValue({
    threads: [{ thread_id: "thread-1", workspace_id: "ws-1", session_name: "s", workspace_path: "/t",
      created_at: "", updated_at: "" }],
    outline: null, detail_outline: null, characters: null, novel: null, worldview: null,
    storyline: { index_markdown: "# 主线\n既有大纲", entries: [] },
  }),
  fetchThreadTraces: vi.fn().mockResolvedValue([]),
  fetchTraceDetail: vi.fn().mockResolvedValue(null),
  fetchThreadRunState: vi.fn().mockResolvedValue({ trace_id: null, status: "idle", awaiting: null }),
  apiFetch: vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve("") }),
  fetchThreadConversation: vi.fn().mockResolvedValue([]),
  createThread: vi.fn().mockResolvedValue({ thread_id: "thread-1", workspace_id: "ws-1", session_name: "s", workspace_path: "/t", created_at: "", updated_at: "" }),
  updateThread: vi.fn().mockResolvedValue({ thread_id: "thread-1" }),
  deleteThread: vi.fn().mockResolvedValue({}),
  createWorkspace: vi.fn(), deleteWorkspace: vi.fn(),
  activateStyle: vi.fn(), createStyle: vi.fn(), updateStyle: vi.fn(), deleteStyle: vi.fn(), optimizeStyle: vi.fn(),
  deleteTrace: vi.fn(), logout: vi.fn(), trackCopy: vi.fn(), trackRegenerate: vi.fn(),
  workspaceNovelPdfUrl: () => "", workspaceNovelWordUrl: () => "",
}));

vi.mock("@/lib/usePanelPolling", () => ({ usePanelPolling: () => {}, watchPipelineAfterDisconnect: () => {}, stopDisconnectWatch: () => {} }));

vi.mock("@/components/workspace/AppShell", () => ({ AppShell: ({ children }: { children: ReactNode }) => <div>{children}</div> }));
vi.mock("@/components/workspace/TopBar", () => ({ TopBar: () => null }));
vi.mock("@/components/workspace/Sidebar", () => ({ Sidebar: () => null }));
vi.mock("@/components/workspace/StyleModal", () => ({ StyleModal: () => null }));
vi.mock("@/components/workspace/ConfirmDialog", () => ({ ConfirmDialog: () => null }));
vi.mock("@/components/workspace/TracePanel", () => ({ TracePanel: () => null }));
vi.mock("@/components/workspace/NovelPanel", () => ({ NovelPanel: () => null }));
vi.mock("@/components/workspace/DetailOutlinePanel", () => ({ DetailOutlinePanel: () => null }));
vi.mock("@/components/workspace/CharactersPanel", () => ({ CharactersPanel: () => null }));
vi.mock("@/components/workspace/WorldviewPanel", () => ({ WorldviewPanel: () => null }));
vi.mock("@/components/workspace/StorylinePanel", () => ({ StorylinePanel: () => null }));
vi.mock("@/components/workspace/InterviewOptions", () => ({ InterviewOptions: () => null }));

const { MemoryRouter } = await import("react-router-dom");
const { render } = await import("@testing-library/react");
const Home = (await import("@/pages/home")).default;

describe("FR-006 会话排队与 DEC-009 断连补跑", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    streamCalls = [];
    factories = [];
    const { resetStoresWithOutline: resetStores } = await import("./helpers");
    await resetStores();
  });

  it("生成中发送第二条消息 → 排队中 → 前轮完成后接续 completed（AC-006）", async () => {
    const user = userEvent.setup();
    const s1 = { stream: gatedStream(RUN1_FINAL, RUN1_AFTER) };
    const s2 = { stream: gatedStream(RUN2_PREFIX, RUN2_AFTER) };
    factories = [() => s1.stream, () => s2.stream];
    render(<MemoryRouter initialEntries={["/"]}><Home /></MemoryRouter>);

    const input = await screen.findByRole("textbox", {}, { timeout: 10000 });
    await user.type(input, "第一轮：写第三章{Enter}");
    // 生成中再发一条：排队（客户端不再禁发）
    await user.type(input, "第二轮：加个反转{Enter}");

    // 第二条进入排队态
    await waitFor(() => {
      expect(screen.getByText(/排队中/)).toBeInTheDocument();
    }, { timeout: 5000 });
    expect((streamCalls[1]?.body as { prompt?: string }).prompt).toBe("第二轮：加个反转");

    // 第一轮放行完成
    s1.stream.release();
    await waitFor(() => {
      expect(screen.getByText("第一轮完成")).toBeInTheDocument();
    }, { timeout: 5000 });

    // 第二轮自动接续（started 复位占位 → final 完成）
    s2.stream.release();
    await waitFor(() => {
      expect(screen.getByText("第二轮完成")).toBeInTheDocument();
    }, { timeout: 5000 });
    // 两条 assistant 行都落 completed
    const assistants = screen.getAllByText(/完成/);
    expect(assistants.length).toBeGreaterThanOrEqual(2);
  });

  it("排队流断连 → 标记待补跑；下一条消息自动按原 id 补发（DEC-009）", async () => {
    const user = userEvent.setup();
    const s1 = { stream: gatedStream(RUN1_FINAL, RUN1_AFTER) };
    // 第二条：只收到 queued 帧即断流（done 无 final → 断连兜底）
    const s2 = { stream: gatedStream(RUN2_PREFIX, []) };
    factories = [() => s1.stream, () => s2.stream];
    render(<MemoryRouter initialEntries={["/"]}><Home /></MemoryRouter>);

    const input = await screen.findByRole("textbox", {}, { timeout: 10000 });
    await user.type(input, "第一轮：写第三章{Enter}");
    await user.type(input, "排队消息：加个反转{Enter}");
    // 排队帧与断流收尾可能同批到达（瞬态「排队中」不保证可渲染）——直接等稳定态
    await waitFor(() => {
      expect(screen.getByText(/自动补发/)).toBeInTheDocument();
    }, { timeout: 5000 });

    // 补跑流 + 新消息流：先自动补发（同 message_id），再发新消息
    const pausedId = streamCalls[1]?.body.message_id;
    expect(pausedId).toBeTruthy();
    factories = [
      () => gatedStream(FINAL_ONLY("补跑完成"), []),
      () => gatedStream(FINAL_ONLY("新消息完成"), []),
    ];
    await user.type(input, "新消息：下一章{Enter}");

    // 补跑请求带原 message_id + 原话术
    await waitFor(() => {
      expect(streamCalls.length).toBeGreaterThanOrEqual(4);
    }, { timeout: 5000 });
    expect((streamCalls[2]?.body as { prompt?: string; message_id?: string }).prompt).toBe("排队消息：加个反转");
    expect(streamCalls[2]?.body.message_id).toBe(pausedId);
    expect((streamCalls[3]?.body as { prompt?: string }).prompt).toBe("新消息：下一章");

    await waitFor(() => {
      expect(screen.getByText("补跑完成")).toBeInTheDocument();
    }, { timeout: 5000 });
    await waitFor(() => {
      expect(screen.getByText("新消息完成")).toBeInTheDocument();
    }, { timeout: 5000 });
  });

  it("两个排队中断对：下一条消息触发时按序全部补跑、原 id 幂等（review R-001/R-002）", async () => {
    const user = userEvent.setup();
    const s1 = { stream: gatedStream(RUN1_FINAL, RUN1_AFTER) };
    const sA = queuedThenBreakStream();
    const sB = queuedThenBreakStream();
    factories = [() => s1.stream, () => sA, () => sB];
    render(<MemoryRouter initialEntries={["/"]}><Home /></MemoryRouter>);

    const input = await screen.findByRole("textbox", {}, { timeout: 10000 });
    await user.type(input, "第一轮：写第三章{Enter}");
    await user.type(input, "排队甲：加个反转{Enter}");
    await user.type(input, "排队乙：换个视角{Enter}");
    // 两条都稳定挂起在排队态
    await waitFor(() => expect(screen.getAllByText(/排队中/).length).toBe(2), { timeout: 5000 });
    // 一起断连（如应用离线）：两条都成待补跑对
    sA.breakStream();
    sB.breakStream();
    await waitFor(() => expect(screen.getAllByText(/自动补发/).length).toBe(2), { timeout: 5000 });

    const idA = streamCalls[1]?.body.message_id;
    const idB = streamCalls[2]?.body.message_id;
    expect(idA).toBeTruthy();
    expect(idB).toBeTruthy();
    expect(idA).not.toBe(idB);

    // 补跑两条（原 id）+ 新消息一条
    factories = [
      () => gatedStream(FINAL_ONLY("甲补跑完成"), []),
      () => gatedStream(FINAL_ONLY("乙补跑完成"), []),
      () => gatedStream(FINAL_ONLY("新消息完成"), []),
    ];
    await user.type(input, "新消息：下一章{Enter}");
    await waitFor(() => expect(streamCalls.length).toBeGreaterThanOrEqual(6), { timeout: 8000 });
    expect((streamCalls[3]?.body as { prompt?: string; message_id?: string }).prompt).toBe("排队甲：加个反转");
    expect(streamCalls[3]?.body.message_id).toBe(idA);
    expect((streamCalls[4]?.body as { prompt?: string }).prompt).toBe("排队乙：换个视角");
    expect(streamCalls[4]?.body.message_id).toBe(idB);
    expect((streamCalls[5]?.body as { prompt?: string }).prompt).toBe("新消息：下一章");
    await waitFor(() => expect(screen.getByText("甲补跑完成")).toBeInTheDocument(), { timeout: 5000 });
    await waitFor(() => expect(screen.getByText("乙补跑完成")).toBeInTheDocument(), { timeout: 5000 });
    await waitFor(() => expect(screen.getByText("新消息完成")).toBeInTheDocument(), { timeout: 5000 });
  });

  it("停止时排队行落 stopped，不再被下一条消息复活（review R-002 客户端侧）", async () => {
    const user = userEvent.setup();
    const s1 = { stream: gatedStream(RUN1_FINAL, RUN1_AFTER) };
    // 排队后挂住（吐完 queued 帧停在闸门）：排队态稳定，直到停止干预
    const s2 = { stream: gatedStream(RUN2_PREFIX, RUN2_AFTER) };
    factories = [() => s1.stream, () => s2.stream];
    render(<MemoryRouter initialEntries={["/"]}><Home /></MemoryRouter>);

    const input = await screen.findByRole("textbox", {}, { timeout: 10000 });
    await user.type(input, "第一轮：写第三章{Enter}");
    await user.type(input, "排队消息：加个反转{Enter}");
    await waitFor(() => expect(screen.getByText(/排队中/)).toBeInTheDocument(), { timeout: 5000 });

    // 点停止：排队行立即落 stopped（不被断流兜底标成待补跑）
    const stopButton = await screen.findByRole("button", { name: "停止" }, { timeout: 5000 });
    await user.click(stopButton);
    await waitFor(() => expect(screen.getByText(/已停止（排队中/)).toBeInTheDocument(), { timeout: 5000 });

    // 下一条消息：不补跑被停掉的排队消息（只有本条自己的请求）
    factories = [() => gatedStream(FINAL_ONLY("新消息完成"), [])];
    await user.type(input, "新消息：下一章{Enter}");
    await waitFor(() => expect(streamCalls.length).toBe(3), { timeout: 5000 });
    expect((streamCalls[2]?.body as { prompt?: string }).prompt).toBe("新消息：下一章");
    await waitFor(() => expect(screen.getByText("新消息完成")).toBeInTheDocument(), { timeout: 5000 });
  });
});
