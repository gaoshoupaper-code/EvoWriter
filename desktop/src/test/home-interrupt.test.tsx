/**
 * T0b 集成测试：HITL interrupt → resume
 *
 * 验证路径（home.tsx performSubmit interrupt 分支）：
 *   1. 用户发送消息 → SSE 推 interrupt 事件（带 question + options）
 *   2. assistant 消息进入 awaitingInput 态，渲染选项
 *   3. 用户选择选项提交 → resume（body 含 resume + trace_id）
 *   4. 第二次 SSE → final → completed
 *
 * 注意：streamRequest 被调用两次——第一次返回 interrupt 流，第二次返回 final 流。
 * 用 mockImplementation 按调用序号切换不同的 SSE 数据。
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

// 第一次调用：run_start → interrupt（HITL choice）
const INTERRUPT_CHUNKS = encode([
  sseEvent("trace_event", {
    trace_id: "trace-1", event_id: "evt-1", sequence: 1, type: "run_start", status: "running",
    timestamp: "2026-01-01T00:00:00Z", source: "system",
    input: { workspace_id: "ws-1", thread_id: "thread-1", session_name: "测试会话", endpoint: "screenplay.generate.stream" },
  }),
  sseEvent("interrupt", {
    kind: "choice",
    question: "第 3 章主角是否遇到反派？",
    options: [
      { label: "遇到，加些冲突", description: "让主角在此章遇到反派" },
      { label: "先铺垫情绪", description: "下章再遇反派" },
    ],
    multi_select: false,
    source: "writing-subagent",
  }),
]);

// 第二次调用（resume）：stream + final（FR-005/006：响应已无 markdown/evaluation_markdown 字段）
const FINAL_CHUNKS = encode([
  sseEvent("model_stream", { content: "正文完成" }),
  sseEvent("final", {
    mode: "screenplay", thread_id: "thread-1", workspace_id: "ws-1", session_name: "测试会话",
    workspace_path: "/test", title: "T", content: "正文完成", logline: "", synopsis: "",
    beats: [],
  }),
]);

// streamRequest 按调用序号从 streams 队列取流；每个用例可在 beforeEach 之后自行注入
// （默认 [INTERRUPT_CHUNKS, FINAL_CHUNKS] 维持 T0b 既有两用例的行为）
let streams: Uint8Array[][] = [];
vi.mock("@/lib/stream", () => ({
  streamRequest: vi.fn().mockImplementation(() => {
    const chunks = streams.shift() ?? FINAL_CHUNKS;
    let index = 0;
    return Promise.resolve({
      read: () => {
        if (index < chunks.length) return Promise.resolve({ done: false, value: chunks[index++] });
        return Promise.resolve({ done: true, value: undefined });
      },
      cancel: () => Promise.resolve(),
    });
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
    threads: [{ thread_id: "thread-1", workspace_id: "ws-1", session_name: "测试会话", workspace_path: "/t",
      created_at: "", updated_at: "" }],
    outline: null, detail_outline: null, characters: null, novel: null, worldview: null,
    // v9（FR-004）：模拟大纲已产出——ChatPanel 修订入口解锁
    storyline: { index_markdown: "# 主线\n既有大纲", entries: [] },
  }),
  fetchThreadTraces: vi.fn().mockResolvedValue([]),
  fetchTraceDetail: vi.fn().mockResolvedValue(null),
  createThread: vi.fn().mockResolvedValue({ thread_id: "thread-1", workspace_id: "ws-1", session_name: "测试会话", workspace_path: "/t", created_at: "", updated_at: "" }),
  updateThread: vi.fn().mockResolvedValue({ thread_id: "thread-1" }),
  deleteThread: vi.fn().mockResolvedValue({}),
  createWorkspace: vi.fn(), deleteWorkspace: vi.fn(),
  activateStyle: vi.fn(), createStyle: vi.fn(), updateStyle: vi.fn(), deleteStyle: vi.fn(), optimizeStyle: vi.fn(),
  deleteTrace: vi.fn(), logout: vi.fn(), trackCopy: vi.fn(), trackRegenerate: vi.fn(),
  workspaceNovelPdfUrl: () => "", workspaceNovelWordUrl: () => "",
}));

vi.mock("@/lib/usePanelPolling", () => ({ usePanelPolling: () => {} }));

vi.mock("@/components/workspace/AppShell", () => ({ AppShell: ({ children }: { children: ReactNode }) => <div>{children}</div> }));
vi.mock("@/components/workspace/TopBar", () => ({ TopBar: () => null }));
vi.mock("@/components/workspace/Sidebar", () => ({ Sidebar: () => null }));
vi.mock("@/components/workspace/StyleModal", () => ({ StyleModal: () => null }));
vi.mock("@/components/workspace/ConfirmDialog", () => ({ ConfirmDialog: () => null }));
vi.mock("@/components/workspace/TracePanel", () => ({ TracePanel: () => null }));
vi.mock("@/components/workspace/NovelPanel", () => ({ NovelPanel: () => null }));
vi.mock("@/components/workspace/ScriptPanel", () => ({ ScriptPanel: () => null }));
vi.mock("@/components/workspace/DetailOutlinePanel", () => ({ DetailOutlinePanel: () => null }));
vi.mock("@/components/workspace/CharactersPanel", () => ({ CharactersPanel: () => null }));
vi.mock("@/components/workspace/WorldviewPanel", () => ({ WorldviewPanel: () => null }));
vi.mock("@/components/workspace/StorylinePanel", () => ({ StorylinePanel: () => null }));
// InterviewOptions mock 为可交互占位：渲染选项按钮
vi.mock("@/components/workspace/InterviewOptions", () => ({
  InterviewOptions: ({ options, onSubmit }: { options: { label: string }[]; onSubmit: (t: string) => void }) => (
    <div data-testid="interview-options">
      {options.map((o, i) => (
        <button key={i} type="button" onClick={() => onSubmit(o.label)}>{o.label}</button>
      ))}
    </div>
  ),
}));
vi.mock("@/components/workspace/ImageReviewCard", () => ({ ImageReviewCard: () => null }));
vi.mock("@/components/workspace/SessionMenu", () => ({ SessionMenu: () => null }));

const { MemoryRouter } = await import("react-router-dom");
const { render } = await import("@testing-library/react");
const Home = (await import("@/pages/home")).default;

describe("T0b: HITL interrupt → resume", () => {
  beforeEach(async () => {
    streams = [INTERRUPT_CHUNKS, FINAL_CHUNKS];
    vi.clearAllMocks();
    const { resetStoresWithOutline: resetStores } = await import("./helpers");
    await resetStores();
  });

  it("interrupt 后出现选项，用户选择后 resume，最终 completed", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/"]}><Home /></MemoryRouter>);

    // 第一次提交
    const input = await screen.findByRole("textbox", {}, { timeout: 10000 });
    await user.type(input, "写一个故事");
    await user.keyboard("{Enter}");

    // interrupt 后出现选项
    const options = await screen.findByTestId("interview-options", {}, { timeout: 10000 });
    expect(options).toBeInTheDocument();
    expect(screen.getByText("第 3 章主角是否遇到反派？")).toBeInTheDocument();
    expect(screen.getByText("遇到，加些冲突")).toBeInTheDocument();

    // FR-005（REQ-20261001-170627）：卡点带产生时间，供本地恢复按 2h 阈值降级
    const { useExecutionStore } = await import("@/stores/execution");
    const lastMsg = [...useExecutionStore.getState().messages].reverse().find((m) => m.role === "assistant");
    expect(lastMsg?.awaitingInput?.askedAt).toBeTruthy();
    expect(Date.parse(lastMsg!.awaitingInput!.askedAt!)).not.toBeNaN();

    // 用户选择选项（触发 resume）
    await user.click(screen.getByText("遇到，加些冲突"));

    // resume 后最终 completed
    await waitFor(() => {
      expect(screen.getByText("正文完成")).toBeInTheDocument();
    }, { timeout: 10000 });

    const messages = document.querySelectorAll(".message.assistant");
    const lastAssistant = messages[messages.length - 1];
    expect(lastAssistant).toHaveAttribute("data-status", "completed");
  });

  it("第二次 streamRequest 调用是 resume 模式（body 含 resume 字段而非 prompt）", async () => {
    const { streamRequest } = await import("@/lib/stream");
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/"]}><Home /></MemoryRouter>);

    const input = await screen.findByRole("textbox", {}, { timeout: 10000 });
    await user.type(input, "写故事");
    await user.keyboard("{Enter}");

    await screen.findByTestId("interview-options", {}, { timeout: 10000 });
    await user.click(screen.getByText("遇到，加些冲突"));

    // 等第二次调用（resume）
    await waitFor(() => {
      expect(streamRequest).toHaveBeenCalledTimes(2);
    }, { timeout: 10000 });

    const secondCall = (streamRequest as ReturnType<typeof vi.fn>).mock.calls[1];
    expect(secondCall[0]).toBe("/api/screenplay/generate/stream");
    expect(secondCall[1].body).toHaveProperty("resume", "遇到，加些冲突");
    expect(secondCall[1].body).toHaveProperty("trace_id", "trace-1");
    // resume 模式不应有 prompt 字段
    expect(secondCall[1].body).not.toHaveProperty("prompt");
  });
});

/**
 * REQ-20261009-002227：提案轮正常关流不得误报「连接中断」。
 *
 * 服务端 interrupt 后 return 正常关闭 SSE 流（等用户选方案再 resume）。
 * 修复前：前端主创作流收到 interrupt 未标记终态，流关闭后落入 FR-002
 * 断流兜底——误弹「连接中断（未收到完成信号）」并把消息标 failed。
 */
describe("FR-001（REQ-20261009-002227）: interrupt 后正常关流不误报断连", () => {
  beforeEach(async () => {
    streams = [INTERRUPT_CHUNKS, FINAL_CHUNKS];
    vi.clearAllMocks();
    const { resetStoresWithOutline: resetStores } = await import("./helpers");
    await resetStores();
  });

  async function submitAndWaitIdle(text: string) {
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/"]}><Home /></MemoryRouter>);
    const input = await screen.findByRole("textbox", {}, { timeout: 10000 });
    await user.type(input, text);
    await user.keyboard("{Enter}");
    // 兜底判定发生在流关闭之后、finally 复位 loading 之前——等 loading 归 false
    // 即代表本轮流（含兜底分支，如触发）已全部执行完。
    const { useExecutionStore } = await import("@/stores/execution");
    await waitFor(() => {
      expect(useExecutionStore.getState().loading).toBe(false);
    }, { timeout: 10000 });
    const lastMsg = [...useExecutionStore.getState().messages].reverse().find((m) => m.role === "assistant");
    const domLast = document.querySelectorAll(".message.assistant");
    return { lastMsg, domLast: domLast[domLast.length - 1] };
  }

  it("AC-001 主路径：interrupt 后流关闭，不落 failed、不报连接中断、awaitingInput 保留", async () => {
    const { lastMsg, domLast } = await submitAndWaitIdle("写一个故事");

    expect(screen.getByTestId("interview-options")).toBeInTheDocument();
    expect(lastMsg?.status).not.toBe("failed");
    expect(lastMsg?.content).not.toContain("连接中断");
    expect(lastMsg?.awaitingInput?.kind).toBe("choice");
    expect(lastMsg?.awaitingInput?.options?.length).toBe(2);
    expect(domLast).not.toHaveAttribute("data-status", "failed");
  });

  it("AC-001 边界：interrupt 无 options（options: null）同样不误报", async () => {
    streams = [encode([
      sseEvent("trace_event", {
        trace_id: "trace-1", event_id: "evt-1", sequence: 1, type: "run_start", status: "running",
        timestamp: "2026-01-01T00:00:00Z", source: "system",
        input: { workspace_id: "ws-1", thread_id: "thread-1", session_name: "测试会话", endpoint: "screenplay.generate.stream" },
      }),
      sseEvent("interrupt", { kind: "choice", question: "要调整哪个方向？", options: null, multi_select: false, source: "interview" }),
    ])];

    const { lastMsg } = await submitAndWaitIdle("写一个故事");

    expect(lastMsg?.status).not.toBe("failed");
    expect(lastMsg?.content).not.toContain("连接中断");
    expect(lastMsg?.awaitingInput?.question).toBe("要调整哪个方向？");
    expect(lastMsg?.awaitingInput?.options).toBeNull();
  });

  it("AC-001 边界：multi_select=true 的 interrupt 不误报且标志保留", async () => {
    streams = [encode([
      sseEvent("trace_event", {
        trace_id: "trace-1", event_id: "evt-1", sequence: 1, type: "run_start", status: "running",
        timestamp: "2026-01-01T00:00:00Z", source: "system",
        input: { workspace_id: "ws-1", thread_id: "thread-1", session_name: "测试会话", endpoint: "screenplay.generate.stream" },
      }),
      sseEvent("interrupt", {
        kind: "choice", question: "选择要保留的版本", options: [
          { label: "版本 A", description: "" }, { label: "版本 B", description: "" },
        ], multi_select: true, source: "interview",
      }),
    ])];

    const { lastMsg } = await submitAndWaitIdle("写一个故事");

    expect(lastMsg?.status).not.toBe("failed");
    expect(lastMsg?.content).not.toContain("连接中断");
    expect(lastMsg?.awaitingInput?.multi_select).toBe(true);
  });

  it("AC-001 补充：model_stream 叙述后收到 interrupt，正文必须是提案问题而非叙述文本", async () => {
    // 复刻线上形态（2026-10-09 彻查）：agent 先流式输出叙述（已读取需求…端出方案），
    // 再发 interrupt。修复前每轮 chunk 后的无条件覆盖 set 会把 interrupt 写入的
    // 提案问题盖回 streamedText——用户只看到叙述 + 连接中断，看不到问题文本。
    streams = [encode([
      sseEvent("trace_event", {
        trace_id: "trace-1", event_id: "evt-1", sequence: 1, type: "run_start", status: "running",
        timestamp: "2026-01-01T00:00:00Z", source: "system",
        input: { workspace_id: "ws-1", thread_id: "thread-1", session_name: "测试会话", endpoint: "screenplay.generate.stream" },
      }),
      sseEvent("model_stream", { content: "已读取需求，设计做完了，端出三套方案供拍板。" }),
      sseEvent("interrupt", {
        kind: "choice", question: "请从三套方案中选择：", options: [
          { label: "方案一", description: "重生复仇流" },
          { label: "方案二", description: "无敌碾压流" },
        ], multi_select: false, source: "interview",
      }),
    ])];

    const { lastMsg } = await submitAndWaitIdle("写一个故事");

    expect(lastMsg?.status).not.toBe("failed");
    expect(lastMsg?.content).not.toContain("连接中断");
    // 提案问题的正文不得被叙述文本覆盖
    expect(lastMsg?.content).toBe("请从三套方案中选择：");
    expect(lastMsg?.awaitingInput?.question).toBe("请从三套方案中选择：");
  });

  it("AC-002 回归：无 interrupt 无 final 的真断流，兜底报错行为保留", async () => {
    streams = [encode([
      sseEvent("trace_event", {
        trace_id: "trace-1", event_id: "evt-1", sequence: 1, type: "run_start", status: "running",
        timestamp: "2026-01-01T00:00:00Z", source: "system",
        input: { workspace_id: "ws-1", thread_id: "thread-1", session_name: "测试会话", endpoint: "screenplay.generate.stream" },
      }),
    ])];

    const { lastMsg, domLast } = await submitAndWaitIdle("写一个故事");

    expect(lastMsg?.status).toBe("failed");
    expect(lastMsg?.content).toContain("连接中断");
    expect(domLast).toHaveAttribute("data-status", "failed");
  });
});

/**
 * 根因回归（2026-10-09 彻查）：SSE 帧跨 TCP chunk 分片不得丢帧。
 *
 * 线上现象：提案轮 interrupt 帧体积超一个 TCP 分段，event: 行与 data: 行落在
 * 不同 chunk。旧实现按行切分且 eventType 每 chunk 重置——data: 行因 eventType
 * 为空被静默丢弃，interrupt 从未到达 UI，用户只看到「连接中断」。
 * 本组用例把完整事件流在任意字节边界切开喂给 reader，验证帧级解析。
 */
describe("SSE 帧跨 chunk 分片解析（根因回归）", () => {
  const RUN_START_FRAME = sseEvent("trace_event", {
    trace_id: "trace-1", event_id: "evt-1", sequence: 1, type: "run_start", status: "running",
    timestamp: "2026-01-01T00:00:00Z", source: "system",
    input: { workspace_id: "ws-1", thread_id: "thread-1", session_name: "测试会话", endpoint: "screenplay.generate.stream" },
  });

  /** 把完整 SSE 文本在给定字节偏移处切成多个 chunk，模拟 TCP 分段。 */
  function splitAtBytes(text: string, cutPoints: number[]): Uint8Array[] {
    const bytes = encoder.encode(text);
    const cuts = [...new Set(cutPoints)].filter((c) => c > 0 && c < bytes.length).sort((a, b) => a - b);
    const chunks: Uint8Array[] = [];
    let prev = 0;
    for (const cut of cuts) {
      chunks.push(bytes.slice(prev, cut));
      prev = cut;
    }
    chunks.push(bytes.slice(prev));
    return chunks;
  }

  beforeEach(async () => {
    vi.clearAllMocks();
    const { resetStoresWithOutline: resetStores } = await import("./helpers");
    await resetStores();
  });

  async function submitAndWaitIdle(text: string) {
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/"]}><Home /></MemoryRouter>);
    const input = await screen.findByRole("textbox", {}, { timeout: 10000 });
    await user.type(input, text);
    await user.keyboard("{Enter}");
    const { useExecutionStore } = await import("@/stores/execution");
    await waitFor(() => {
      expect(useExecutionStore.getState().loading).toBe(false);
    }, { timeout: 10000 });
    const lastMsg = [...useExecutionStore.getState().messages].reverse().find((m) => m.role === "assistant");
    return { lastMsg };
  }

  it("interrupt 帧在 event: 行与 data: 行之间切开——问题仍到达 UI，不报连接中断", async () => {
    const interruptFrame = sseEvent("interrupt", {
      kind: "choice", question: "请从三套方向提案中拍板：", options: [
        { label: "方案一", description: "孤岛心理悬疑《灯下之影》" },
        { label: "方案二", description: "深海温情奇幻《光与岸》" },
      ], multi_select: false, source: "interview",
    });
    // 切点正好落在 "event: interrupt\n" 之后——旧实现的确定性丢帧点。
    // 偏移按字节算（中文多字节，字符数 ≠ 字节数）。
    const cut = encoder.encode(RUN_START_FRAME).length + encoder.encode("event: interrupt\n").length;
    streams = [splitAtBytes(RUN_START_FRAME + interruptFrame, [cut])];

    const { lastMsg } = await submitAndWaitIdle("写一个故事");

    expect(lastMsg?.status).not.toBe("failed");
    expect(lastMsg?.content).not.toContain("连接中断");
    expect(lastMsg?.content).toBe("请从三套方向提案中拍板：");
    expect(lastMsg?.awaitingInput?.options?.length).toBe(2);
    expect(lastMsg?.awaitingInput?.options?.[0]?.description).toBe("孤岛心理悬疑《灯下之影》");
  });

  it("interrupt 帧在 data 行中间切开（大帧多分段）——问题完整无乱码", async () => {
    const interruptFrame = sseEvent("interrupt", {
      kind: "choice", question: "提案轮：三套方案均为短篇悬念梗概，选定即动笔，不再追问细节。你要哪套？", options: [
        { label: "选定方案 1", description: "孤岛心理悬疑《灯下之影》" },
        { label: "选定方案 2", description: "深海温情奇幻《光与岸》" },
        { label: "选定方案 3", description: "风暴夜救援惊悚《引航员》" },
        { label: "我要调整", description: "调整方向或混合元素，先补充" },
      ], multi_select: false, source: "interview",
    });
    // 在 data 行正中间多切几刀（字节级，含多字节中文字符中间）
    const dataStart = encoder.encode(RUN_START_FRAME).length + encoder.encode("event: interrupt\ndata: ").length;
    const dataLen = encoder.encode(interruptFrame).length - encoder.encode("event: interrupt\ndata: ").length - 2;
    streams = [splitAtBytes(RUN_START_FRAME + interruptFrame, [
      dataStart + Math.floor(dataLen / 3),
      dataStart + Math.floor(dataLen * 2 / 3),
    ])];

    const { lastMsg } = await submitAndWaitIdle("写一个故事");

    expect(lastMsg?.status).not.toBe("failed");
    expect(lastMsg?.content).not.toContain("连接中断");
    expect(lastMsg?.content).not.toContain("\uFFFD");
    expect(lastMsg?.awaitingInput?.question).toContain("选定即动笔");
    expect(lastMsg?.awaitingInput?.options?.length).toBe(4);
  });

  it("final 帧在 event: 行与 data: 行之间切开——落 completed 而非连接中断", async () => {
    const modelFrame = sseEvent("model_stream", { content: "正文完成" });
    const finalFrame = sseEvent("final", {
      mode: "screenplay", thread_id: "thread-1", workspace_id: "ws-1", session_name: "测试会话",
      workspace_path: "/test", title: "T", content: "正文完成", logline: "", synopsis: "",
      beats: [],
    });
    const prefix = RUN_START_FRAME + modelFrame;
    const cut = encoder.encode(prefix).length + encoder.encode("event: final\n").length;
    streams = [splitAtBytes(prefix + finalFrame, [cut])];

    const { lastMsg } = await submitAndWaitIdle("写一个故事");

    expect(lastMsg?.status).toBe("completed");
    expect(lastMsg?.content).not.toContain("连接中断");
    expect(lastMsg?.content).toBe("正文完成");
  });

  it("model_stream 帧 data 行中间切开——叙述文本完整拼接", async () => {
    const modelFrame1 = sseEvent("model_stream", { content: "提案轮设计完成，" });
    const modelFrame2 = sseEvent("model_stream", { content: "端出三套方向供拍板。" });
    const prefix = RUN_START_FRAME + modelFrame1;
    const cut = encoder.encode(prefix).length + encoder.encode("event: model_stream\ndata: ").length + 6;
    streams = [splitAtBytes(prefix + modelFrame2, [cut])];

    const { lastMsg } = await submitAndWaitIdle("写一个故事");

    // 无 final 无 interrupt → 走 FR-002 兜底，但叙述文本必须完整（旧实现会缺帧）
    expect(lastMsg?.content).toContain("提案轮设计完成，端出三套方向供拍板。");
  });
});
