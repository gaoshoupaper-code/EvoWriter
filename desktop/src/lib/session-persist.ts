/**
 * session-persist —— 会话聊天记录本地持久化（REQ-20261001-170627）
 *
 * 职责：把 executionStore 的消息列表持久化到 Tauri 应用数据目录
 * （tauri-plugin-store，DEC-005），提供恢复管线、删除联动与上次位置记忆。
 *
 * 数据布局（FR-002：按登录账号隔离，Rust 侧零新增代码）：
 * - store 文件：sessions/<encodeURIComponent(username)>.store
 * - 会话 key：msg:<workspace_id>:<thread_id> → { messages, savedAt }
 * - 上次位置 key：__last__ → { workspaceId, threadId }（FR-001）
 *
 * 写入时序（风险条目：终版不被在途节流覆盖）：
 * - 流式期间 scheduleThreadPersist 节流合并（快照始终取最新）；
 * - 终态 flushThreadPersist 取消 pending 节流并立即入队；
 * - 内部 promise 链串行化所有落盘，先入队的中间版先写、终版最后写。
 *
 * 失败语义（FR-002）：所有读写失败静默降级——写失败不阻塞聊天主流程，
 * 读失败返回 null（调用方回退欢迎语），永不向调用方抛异常。
 */
import { load, type Store } from "@tauri-apps/plugin-store";
import type { ChatMessage } from "@/lib/types";
import type { DemandFields } from "@/lib/demand";

/** 卡点有效期，与服务器 awaiting_input 超时（2h → cancelled）对齐；改动需两侧同步。 */
export const AWAITING_INPUT_TIMEOUT_MS = 2 * 60 * 60 * 1000;

/** 流式期间节流写盘间隔（FR-002：中断后已生成部分可恢复）。 */
const PERSIST_THROTTLE_MS = 1500;

// ── 账号隔离（FR-002：不同账号互不可见）──

let getUsername: () => string | null | undefined = () => null;

/** 注入登录用户名 getter（workspace store authUser.username）。幂等可重复调用。 */
export function initSessionPersist(deps: { getUsername: () => string | null | undefined }) {
  getUsername = deps.getUsername;
}

function currentUserPath(): string | null {
  const username = getUsername();
  if (!username) return null;
  // encodeURIComponent 产物不含 Windows 非法字符，直接做安全文件名
  return `sessions/${encodeURIComponent(username)}.store`;
}

// ── store 实例缓存（per username）──

let storePromise: Promise<Store> | null = null;
let storePath = "";

async function getStore(): Promise<Store | null> {
  const path = currentUserPath();
  if (!path) return null;
  if (storePromise && storePath !== path) {
    // 切账号：旧 store 不主动 close（进程内可能仍有在途写入），直接换实例
    storePromise = null;
  }
  storePath = path;
  if (!storePromise) storePromise = load(path, { defaults: {}, autoSave: 100 });
  return storePromise;
}

function threadKey(workspaceId: string, threadId: string) {
  return `msg:${workspaceId}:${threadId}`;
}

const WORKSPACE_KEY_PREFIX = "msg:";

// ── 恢复管线（FR-004/FR-005，纯函数便于测试）──

/**
 * 把持久化时点的消息列表转为「重启恢复」形态：
 * - streaming → stopped（StoppedView 自带重新生成入口，FR-004）
 * - running tool → done（避免陈旧 running 卡出 thinking 态）
 * - executionPhase 清除（瞬态派生值，恢复后由 derivePhaseFromMessage 重算）
 * - 卡点超 2h 或缺 askedAt → 去 awaitingInput 降级只读（FR-005；
 *   问题文本已在 content 中，仍可阅读）
 */
export function normalizeRestoredMessages(messages: ChatMessage[], now = Date.now()): ChatMessage[] {
  if (!Array.isArray(messages)) return [];
  return messages.map((m) => {
    const next: ChatMessage = { ...m };
    if (next.status === "streaming") next.status = "stopped";
    if (next.tools?.some((t) => t.status === "running")) {
      next.tools = next.tools.map((t) => (t.status === "running" ? { ...t, status: "done" as const } : t));
    }
    // 思考段恢复（FR-005，REQ-20261010-182114）：持久化时点仍在 streaming 的
    // 思考段，重启后不可能再续——标 interrupted（DEC-007：保留到中断点可回看）
    if (next.thoughts?.some((t) => t.status === "streaming")) {
      next.thoughts = next.thoughts.map((t) =>
        t.status === "streaming" ? { ...t, status: "interrupted" as const } : t,
      );
    }
    delete next.executionPhase;
    if (next.awaitingInput) {
      const askedAt = next.awaitingInput.askedAt ? Date.parse(next.awaitingInput.askedAt) : NaN;
      const expired = Number.isNaN(askedAt) || now - askedAt > AWAITING_INPUT_TIMEOUT_MS;
      if (expired) next.awaitingInput = undefined;
    }
    return next;
  });
}

// ── 读写 ──

interface PersistedThread {
  messages: ChatMessage[];
  savedAt: string;
}

/** 读回一个会话的消息（含恢复管线）。未命中/失败返回 null。 */
export async function restoreThreadMessages(workspaceId: string, threadId: string): Promise<ChatMessage[] | null> {
  try {
    const store = await getStore();
    if (!store) return null;
    const record = await store.get<PersistedThread>(threadKey(workspaceId, threadId));
    if (!record || !Array.isArray(record.messages)) return null;
    const normalized = normalizeRestoredMessages(record.messages);
    return normalized.length ? normalized : null;
  } catch {
    return null;
  }
}

// ── 写入调度（节流 + 串行落盘）──

interface PersistPayload {
  workspaceId: string;
  threadId: string;
  messages: ChatMessage[];
}

let pendingPayload: PersistPayload | null = null;
let pendingTimer: ReturnType<typeof setTimeout> | null = null;
let writeChain: Promise<void> = Promise.resolve();

function enqueueWrite(payload: PersistPayload) {
  writeChain = writeChain
    .then(async () => {
      const store = await getStore();
      if (!store) return;
      const record: PersistedThread = { messages: payload.messages, savedAt: new Date().toISOString() };
      await store.set(threadKey(payload.workspaceId, payload.threadId), record);
      await store.save();
    })
    .catch(() => {
      /* FR-002 失败语义：写失败静默，不阻塞聊天 */
    });
}

/** 流式期间节流写中间内容（FR-002）。快照取最新调用，天然合并。 */
export function scheduleThreadPersist(workspaceId: string, threadId: string, messages: ChatMessage[]) {
  pendingPayload = { workspaceId, threadId, messages };
  if (pendingTimer) clearTimeout(pendingTimer);
  pendingTimer = setTimeout(() => {
    pendingTimer = null;
    const payload = pendingPayload;
    pendingPayload = null;
    if (payload) enqueueWrite(payload);
  }, PERSIST_THROTTLE_MS);
}

/**
 * 终态立即落盘：取消 pending 节流、以给定终版快照入队。
 * 串行链保证终版在所有中间版之后写入，不会被覆盖。
 * 入参缺省时落 pending 中的最新快照。返回落盘完成的 promise。
 */
export function flushThreadPersist(messages?: ChatMessage[], workspaceId?: string, threadId?: string): Promise<void> {
  if (pendingTimer) {
    clearTimeout(pendingTimer);
    pendingTimer = null;
  }
  const payload =
    messages != null && workspaceId && threadId
      ? { workspaceId, threadId, messages }
      : pendingPayload;
  pendingPayload = null;
  if (payload) enqueueWrite(payload);
  return writeChain;
}

/** 同步直写（不经节流），供低频调用（测试/迁移语义）使用。 */
export async function persistThreadMessages(workspaceId: string, threadId: string, messages: ChatMessage[]) {
  enqueueWrite({ workspaceId, threadId, messages });
  await writeChain;
}

// ── 删除联动（FR-003）──

/** 删除单个会话的本地记录。 */
export async function deleteThreadRecord(workspaceId: string, threadId: string) {
  try {
    const store = await getStore();
    if (!store) return;
    await store.delete(threadKey(workspaceId, threadId));
    await store.save();
  } catch {
    /* FR-003 失败语义：本地删除失败不阻断，残留按名单不存在对待 */
  }
}

/** 删除整个工作区下全部会话的本地记录。 */
export async function deleteWorkspaceRecords(workspaceId: string) {
  try {
    const store = await getStore();
    if (!store) return;
    const prefix = `${WORKSPACE_KEY_PREFIX}${workspaceId}:`;
    const entries = await store.entries<[string, unknown]>();
    for (const [key] of entries) {
      if (key.startsWith(prefix)) await store.delete(key);
    }
    await store.save();
  } catch {
    /* 同上 */
  }
}

// ── 需求表单记忆（FR-001，REQ-20261009-224433）──

interface LastDemandRecord {
  fields: DemandFields;
  savedAt: string;
}

const LAST_DEMAND_KEY = "__last_demand__";

/** 记住本次提交的表单字段，供下次新建会话预填。写失败静默，不阻塞提交流程。 */
export async function saveLastDemandFields(fields: DemandFields): Promise<void> {
  try {
    const store = await getStore();
    if (!store) return;
    await store.set(LAST_DEMAND_KEY, { fields, savedAt: new Date().toISOString() } satisfies LastDemandRecord);
    await store.save();
  } catch {
    /* 记忆失败静默：下次退化为空白表单 */
  }
}

/** 读上次提交的表单字段（预填用）。未记录 / 数据损坏 / 读失败返回 null。 */
export async function readLastDemandFields(): Promise<DemandFields | null> {
  try {
    const store = await getStore();
    if (!store) return null;
    const record = await store.get<LastDemandRecord>(LAST_DEMAND_KEY);
    if (!record?.fields || typeof record.fields !== "object") return null;
    return record.fields;
  } catch {
    return null;
  }
}

// ── 上次位置（FR-001）──

interface LastPosition {
  workspaceId: string;
  threadId: string;
}

export async function saveLastPosition(workspaceId: string, threadId: string) {
  try {
    const store = await getStore();
    if (!store) return;
    await store.set("__last__", { workspaceId, threadId } satisfies LastPosition);
    await store.save();
  } catch {
    /* 位置记忆失败静默：回退现状默认选择 */
  }
}

export async function readLastPosition(): Promise<LastPosition | null> {
  try {
    const store = await getStore();
    if (!store) return null;
    const value = await store.get<LastPosition>("__last__");
    if (!value || !value.workspaceId || !value.threadId) return null;
    return value;
  } catch {
    return null;
  }
}

/** 测试专用：清空模块级缓存（store 实例/节流/串行链），保证用例间隔离。 */
export function _resetPersistForTests() {
  if (pendingTimer) {
    clearTimeout(pendingTimer);
    pendingTimer = null;
  }
  pendingPayload = null;
  storePromise = null;
  storePath = "";
  writeChain = Promise.resolve();
}
