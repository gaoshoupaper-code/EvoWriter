import { describe, expect, it, vi } from "vitest";
import { fetchThreadConversation } from "@/lib/api";
import { restoreFromServerConversation } from "@/stores/execution";
import { normalizeRestoredMessages } from "@/lib/session-persist";

vi.mock("@/lib/api", () => ({ fetchThreadConversation: vi.fn() }));

describe("补卡消息恢复（AC-006）", () => {
  it("服务端日志的用户与助手标志均保留，旧消息缺字段按普通写作恢复", async () => {
    vi.mocked(fetchThreadConversation).mockResolvedValue([
      { id: "u", role: "user", content: "重做人物卡", status: "completed", character_repair: true },
      { id: "a", role: "assistant", content: "卡片已重建", status: "completed", trace_id: "repair", character_repair: true },
      { id: "old", role: "user", content: "继续写作", status: "completed" },
    ]);
    const restored = await restoreFromServerConversation("thread");
    expect(restored?.map((message) => !!message.characterRepair)).toEqual([true, true, false]);
  });

  it("本地排队消息恢复为待补跑时仍保留补卡标志", () => {
    const restored = normalizeRestoredMessages([
      { role: "user", content: "补卡", characterRepair: true, messageId: "u" },
      { role: "assistant", content: "排队", characterRepair: true, status: "queued" },
    ]);
    expect(restored.every((message) => message.characterRepair)).toBe(true);
    expect(restored[1].queuedInterrupted).toBe(true);
  });
});
