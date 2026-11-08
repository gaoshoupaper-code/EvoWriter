import { describe, expect, it } from "vitest";
import { getTraceDisplay, mergeTraceDisplay } from "./trace-display";

describe("可读运行名称（REQ-20261108-232116）", () => {
  it("组合作品和已记录阶段，不使用普通对话的进度猜章节", () => {
    const display = getTraceDisplay({ session_name: "会话 1", workload: "creation",
      display_context: { workspace_title: "雾城", task_phase: "text" } });
    expect(display.title).toBe("雾城 · 正文创作");
    expect(display.title).not.toContain("第");
  });

  it.each([["theme", "题材"], ["outline", "总纲"], ["volume", "卷纲"], ["chapter", "章纲"], ["text", "正文"]])(
    "翻译真实管线阶段 %s", (task_phase, label) => {
      expect(getTraceDisplay({ display_context: { workspace_title: "雾城", task_phase } }).title).toBe(`雾城 · ${label}创作`);
    });

  it("快照优先于后续改名或页面传入名称", () => {
    expect(getTraceDisplay({ run_snapshot: { display_context: { workspace_title: "旧书名", task_phase: "writing" } } },
      { workspace_title: "新书名" }).title).toBe("旧书名 · 正文创作");
  });

  it("普通创作的 generation 操作不被误标成实验", () => {
    expect(getTraceDisplay({ run_purpose: "user_generation", display_context: {
      workspace_title: "雾城", task_phase: "text", operation: "generation",
    } }).title).toBe("雾城 · 正文创作");
  });

  it("实验主标题与辅助信息分层，重复不是重试", () => {
    const display = getTraceDisplay({ started_at: "2026-11-08T15:30:12Z", display_context: {
      sample_name: "样本A", experiment_name: "对白优化", operation: "evaluation", task_type: "chapter_text",
      phase: "validation", arm: "candidate", round: 2, repeat: 3,
    } });
    expect(display.title).toBe("样本A · 正文评分");
    expect(display.subtitle).toContain("对白优化");
    expect(display.subtitle).toContain("独立终验");
    expect(display.subtitle).toContain("候选第2轮");
    expect(display.subtitle).toContain("第3次重复");
    expect(display.subtitle).toMatch(/2026.*11.*08.*\d{2}:\d{2}:12/);
    expect(display.subtitle).not.toContain("重试");
  });

  it("改版不伪造样本", () => {
    const display = getTraceDisplay({ display_context: { experiment_name: "对白优化", operation: "improvement", round: 2 } });
    expect(display.title).toBe("对白优化 · 第2轮改版");
    expect(display.title).not.toContain("样本");
  });

  it.each(["trace-7bf394a123", "sample-49aabf", "会话 1", "全自动目标任务", "evolve-ab"])("内部或默认名称 %s 不当业务标题", (session_name) => {
    const display = getTraceDisplay({ trace_id: "trace-7bf394a123", session_name });
    expect(display.title).toBe("历史运行");
    expect(display.subtitle).toContain("任务信息未记录");
    expect(display.title + display.subtitle).not.toContain("7bf394");
  });

  it("缺时间不伪造当前时间，同秒运行可以同名", () => {
    expect(getTraceDisplay({ workload: "creation" }).subtitle).toContain("开始时间未记录");
    const run = { started_at: "2026-11-08T15:30:12Z", display_context: { workspace_title: "雾城", task_phase: "writing" } };
    expect(getTraceDisplay({ ...run, trace_id: "trace-a" })).toEqual(getTraceDisplay({ ...run, trace_id: "trace-b" }));
  });

  it("暂缺字段不能覆盖已有名称和时间", () => {
    const next = mergeTraceDisplay({ trace_id: "trace-a", session_name: null, started_at: null, display_context: {} },
      { trace_id: "trace-a", session_name: "已有会话", started_at: "2026-11-08T15:30:12Z",
        display_context: { workspace_title: "雾城", task_phase: "writing" } });
    expect(getTraceDisplay(next).title).toBe("雾城 · 正文创作");
    expect(next.session_name).toBe("已有会话");
    expect(next.started_at).toBe("2026-11-08T15:30:12Z");
  });

  it.each([{}, { display_context: { task_phase: "text" } }])("空或不完整快照不能让历史名称漂移", (run_snapshot) => {
    const next = mergeTraceDisplay({ trace_id: "trace-a", run_snapshot,
      display_context: { workspace_title: "当前新书名", legacy: true } },
    { trace_id: "trace-a", run_snapshot: { display_context: { workspace_title: "运行时旧书名", task_phase: "text" } } });
    expect(getTraceDisplay(next).title).toBe("运行时旧书名 · 正文创作");
  });
});
