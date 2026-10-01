import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  archiveEvolveAgent,
  createEvolveAgent,
  listBindableWorkspaces,
  renameEvolveAgent,
  type BindableWorkspace,
  type EvolveAgent,
} from "@/lib/api";

/**
 * 启动入口卡（idle 视图，REQ-20261001-131018 DEC-002/008/009）。
 *
 * 两段式启动：先选/建进化 Agent（绑作品），再开会话。
 *   - Agent 下拉：active Agent 列表（含作品、会话数、发布数）
 *   - 新建对话框：起名 + 从 executor 全平台作品列表选一个（1:1 绑定，
 *     已被占用时后端 409，提示占用方并引导换作品）
 *   - 选中后：显示 Agent 档案摘要 + 「作品已删除」红色徽章（DEC-010，
 *     删除后不可开新会话）+ 附带评测批次（可选）+ 启动按钮
 */
interface Props {
  agents: EvolveAgent[];
  selectedAgentId: string | null;
  batches: { batch_id: string; harness_version: number | null; status: string }[];
  starting: boolean;
  landingOccupied: boolean;
  onSelectAgent: (agentId: string | null) => void;
  onAgentsChanged: () => void;
  onStart: (agentId: string, benchmarkBatchId: string | null) => void;
}

export default function AgentLaunchCard({
  agents,
  selectedAgentId,
  batches,
  starting,
  landingOccupied,
  onSelectAgent,
  onAgentsChanged,
  onStart,
}: Props) {
  const selectedAgent = agents.find((a) => a.agent_id === selectedAgentId) ?? null;
  const [selectedBatchId, setSelectedBatchId] = useState("");
  const [creating, setCreating] = useState(false);
  const [workspaces, setWorkspaces] = useState<BindableWorkspace[] | null>(null);
  const [newName, setNewName] = useState("");
  const [newWorkspaceId, setNewWorkspaceId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [renaming, setRenaming] = useState(false);

  // 打开新建对话框时拉作品列表（executor 不可达 → 明确报错，不静默）
  useEffect(() => {
    if (!creating) return;
    let cancelled = false;
    listBindableWorkspaces()
      .then((resp) => {
        if (!cancelled) setWorkspaces(resp.workspaces);
      })
      .catch((err) => {
        if (!cancelled) {
          setWorkspaces([]);
          toast.error(
            err instanceof Error ? `作品列表拉取失败：${err.message}` : "作品列表拉取失败",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [creating]);

  const handleCreate = useCallback(async () => {
    const name = newName.trim();
    if (!name || !newWorkspaceId) {
      toast.error("请填写 Agent 名字并选择一个作品");
      return;
    }
    setSubmitting(true);
    try {
      const agent = await createEvolveAgent(name, newWorkspaceId);
      toast.success(`进化 Agent「${agent.name}」已创建并绑定作品`);
      setCreating(false);
      setNewName("");
      setNewWorkspaceId("");
      onAgentsChanged();
      onSelectAgent(agent.agent_id);
    } catch (err) {
      // 409：作品已被绑定——detail 含占用方（DEC-009）
      const detail = (err as any)?.detail;
      if (detail && typeof detail === "object" && detail.bound_agent_name) {
        toast.error(
          `作品已被进化 Agent「${detail.bound_agent_name}」绑定，请换一个作品或进入该 Agent`,
        );
      } else {
        toast.error(err instanceof Error ? err.message : "创建失败");
      }
    } finally {
      setSubmitting(false);
    }
  }, [newName, newWorkspaceId, onAgentsChanged, onSelectAgent]);

  const handleRename = useCallback(async () => {
    if (!selectedAgent) return;
    const next = window.prompt("新的 Agent 名字", selectedAgent.name);
    if (!next || !next.trim() || next.trim() === selectedAgent.name) return;
    try {
      await renameEvolveAgent(selectedAgent.agent_id, next.trim());
      toast.success("已改名");
      onAgentsChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "改名失败");
    }
  }, [selectedAgent, onAgentsChanged]);

  const handleArchive = useCallback(async () => {
    if (!selectedAgent) return;
    if (
      !window.confirm(
        `归档「${selectedAgent.name}」？历史会话将进入「未绑定」归档区，发布记录保留，作品绑定释放。`,
      )
    )
      return;
    try {
      await archiveEvolveAgent(selectedAgent.agent_id);
      toast.success("已归档（绑定已释放，作品可再绑新 Agent）");
      onSelectAgent(null);
      onAgentsChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "归档失败");
    }
  }, [selectedAgent, onSelectAgent, onAgentsChanged]);

  const workDeleted = !!selectedAgent?.work_deleted_at;
  const cannotStartReason = workDeleted
    ? "绑定作品已被删除（DEC-010）——已有会话可继续，但不能开新会话"
    : starting
      ? null
      : null;

  return (
    <section className="conversation-panel idle">
      <div className="start-card">
        <div className="start-icon">🧬</div>
        <h2 className="start-title">启动一次进化共创</h2>
        <p className="start-subtitle">
          进化 Agent 绑定一个作品：它用工具翻看该作品的全部会话记录与产物，
          结合 harness 结构与运作证据，和你讨论怎么改进创作 Agent。
        </p>

        {/* 第一步：选/建 Agent */}
        <div className="start-form">
          <select
            className="trace-select"
            value={selectedAgentId ?? ""}
            onChange={(e) => onSelectAgent(e.target.value || null)}
            disabled={starting}
          >
            <option value="">
              {agents.length === 0 ? "还没有进化 Agent——先新建一个" : "选择进化 Agent…"}
            </option>
            {agents.map((a) => (
              <option key={a.agent_id} value={a.agent_id}>
                {a.name} · 作品 {a.workspace_id.slice(0, 8)}… · 会话 {a.session_count ?? 0}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="start-btn secondary"
            onClick={() => setCreating(true)}
            disabled={starting}
          >
            新建 Agent
          </button>
        </div>

        {/* 选中 Agent：档案摘要 + 可选批次 + 启动 */}
        {selectedAgent && (
          <>
            <div className="agent-summary">
              {workDeleted && (
                <span className="badge badge-danger">作品已删除</span>
              )}
              {renaming ? null : (
                <span className="agent-summary-line">
                  绑定作品 <code>{selectedAgent.workspace_id.slice(0, 12)}…</code>
                  {" · "}
                  发布 {selectedAgent.published_count ?? 0} 次
                  {" · "}
                  创建于 {selectedAgent.created_at.slice(0, 10)}
                </span>
              )}
              <span className="agent-summary-actions">
                <button type="button" className="link-btn" onClick={() => void handleRename()}>
                  改名
                </button>
                <button type="button" className="link-btn danger" onClick={() => void handleArchive()}>
                  归档
                </button>
              </span>
            </div>

            <div className="start-form">
              <select
                className="trace-select"
                value={selectedBatchId}
                onChange={(e) => setSelectedBatchId(e.target.value)}
                disabled={starting || batches.length === 0}
              >
                <option value="">
                  {batches.length === 0 ? "暂无评测批次（可不附带）" : "附带评测批次（可选，补充证据）…"}
                </option>
                {batches.map((b) => (
                  <option key={b.batch_id} value={b.batch_id}>
                    批次 {b.batch_id.slice(0, 8)}… · v{b.harness_version ?? "?"} · {b.status}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="start-btn"
                disabled={starting || !!cannotStartReason}
                onClick={() => onStart(selectedAgent.agent_id, selectedBatchId || null)}
                title={cannotStartReason ?? undefined}
              >
                {starting ? "启动中…" : "开会话"}
              </button>
            </div>
            {cannotStartReason && (
              <p className="start-hint danger">{cannotStartReason}</p>
            )}
            {landingOccupied && (
              <p className="start-hint">
                落地通道当前被占用（finalizing/待审中的会话）——开会话可正常对话，
                拍板落地需等通道释放（DEC-004 聊天并行、落地排队）。
              </p>
            )}
          </>
        )}
      </div>

      {/* 新建 Agent 对话框 */}
      {creating && (
        <div className="modal-overlay" onClick={() => !submitting && setCreating(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <h3 className="modal-title">新建进化 Agent</h3>
            <p className="modal-hint">
              一个 Agent 绑定一个作品（1:1）。它长期负责该作品的 harness 进化：
              进化点跨会话累积，能翻看作品全部会话记录与产物。
            </p>
            <label className="modal-field">
              <span>Agent 名字</span>
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="如：星尘号的进化师"
                maxLength={100}
                disabled={submitting}
              />
            </label>
            <label className="modal-field">
              <span>绑定作品（全平台，显示所属用户）</span>
              <select
                value={newWorkspaceId}
                onChange={(e) => setNewWorkspaceId(e.target.value)}
                disabled={submitting || workspaces === null}
              >
                <option value="">
                  {workspaces === null
                    ? "加载作品列表…"
                    : workspaces.length === 0
                      ? "无可选作品（executor 不可达或无作品）"
                      : "选择作品…"}
                </option>
                {workspaces?.map((w) => (
                  <option key={w.workspace_id} value={w.workspace_id}>
                    {w.title} · {w.owner_username ?? w.owner_user_id.slice(0, 8)} · 会话{" "}
                    {w.session_count}
                  </option>
                ))}
              </select>
            </label>
            <div className="modal-actions">
              <button
                type="button"
                className="start-btn secondary"
                onClick={() => setCreating(false)}
                disabled={submitting}
              >
                取消
              </button>
              <button
                type="button"
                className="start-btn"
                onClick={() => void handleCreate()}
                disabled={submitting || !newName.trim() || !newWorkspaceId}
              >
                {submitting ? "创建中…" : "创建并绑定"}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
