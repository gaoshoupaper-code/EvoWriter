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
 * 新开会话对话框（独立组件，REQ-20261001-131018 交互修正 2026-10-01）。
 *
 * 「开新会话」不依赖工作台是否空闲——工作台常驻显示当前（最新）会话，
 * 旧会话去「进化历史」Tab 取；开新会话永远从本组件进：
 *   1. 选进化 Agent（记住上次选择；作品已删除的 Agent 禁选）
 *   2. 可选附带评测批次（补充证据）
 *   3. 新建 / 改名 / 归档 Agent 的管理入口（内嵌二级对话框）
 */
interface Props {
  open: boolean;
  agents: EvolveAgent[];
  selectedAgentId: string | null;
  batches: { batch_id: string; harness_version: number | null; status: string }[];
  starting: boolean;
  onClose: () => void;
  onSelectAgent: (agentId: string | null) => void;
  onAgentsChanged: () => void;
  onStart: (agentId: string, benchmarkBatchId: string | null) => void;
}

export default function NewSessionDialog({
  open,
  agents,
  selectedAgentId,
  batches,
  starting,
  onClose,
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

  // 对话框打开时重置批次选择（每次开新会话独立决定）
  useEffect(() => {
    if (open) setSelectedBatchId("");
  }, [open]);

  // 打开新建 Agent 二级对话框时拉作品列表（executor 不可达 → 明确报错，不静默）
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
          `作品已被进化 Agent「${detail.bound_agent_name}」绑定，请换一个作品或选择该 Agent`,
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

  if (!open) return null;

  const workDeleted = !!selectedAgent?.work_deleted_at;

  return (
    <div className="modal-overlay" onClick={() => !submitting && !starting && onClose()}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <h3 className="modal-title">新开进化会话</h3>
        <p className="modal-hint">
          在进化 Agent 下开一个新会话。旧会话不受影响——可在「进化历史」里回看。
        </p>

        {/* 第一步：选 Agent + 管理入口 */}
        <label className="modal-field">
          <span>进化 Agent（绑定作品，1:1）</span>
          <div className="agent-picker-row">
            <select
              value={selectedAgentId ?? ""}
              onChange={(e) => onSelectAgent(e.target.value || null)}
              disabled={starting}
            >
              <option value="">
                {agents.length === 0 ? "还没有进化 Agent——先新建" : "选择进化 Agent…"}
              </option>
              {agents.map((a) => (
                <option key={a.agent_id} value={a.agent_id} disabled={!!a.work_deleted_at}>
                  {a.name} · 作品 {a.workspace_id.slice(0, 8)}…
                  {a.work_deleted_at ? "（作品已删除）" : ""} · 会话 {(a.session_count ?? 0) + 1}
                </option>
              ))}
            </select>
            {selectedAgent && (
              <span className="agent-picker-actions">
                <button type="button" className="link-btn" onClick={() => void handleRename()}>
                  改名
                </button>
                <button type="button" className="link-btn danger" onClick={() => void handleArchive()}>
                  归档
                </button>
              </span>
            )}
          </div>
        </label>

        {selectedAgent && workDeleted && (
          <p className="start-hint danger">
            该 Agent 绑定的作品已被删除，不能开新会话（DEC-010）。
          </p>
        )}

        {/* 第二步：可选批次 */}
        {selectedAgent && !workDeleted && (
          <label className="modal-field">
            <span>附带评测批次（可选，补充证据）</span>
            <select
              value={selectedBatchId}
              onChange={(e) => setSelectedBatchId(e.target.value)}
              disabled={starting || batches.length === 0}
            >
              <option value="">
                {batches.length === 0 ? "暂无评测批次（可不附带）" : "不附带"}
              </option>
              {batches.map((b) => (
                <option key={b.batch_id} value={b.batch_id}>
                  批次 {b.batch_id.slice(0, 8)}… · v{b.harness_version ?? "?"} · {b.status}
                </option>
              ))}
            </select>
          </label>
        )}

        <div className="modal-actions">
          <button
            type="button"
            className="start-btn secondary"
            onClick={() => setCreating(true)}
            disabled={starting}
          >
            新建 Agent
          </button>
          <button
            type="button"
            className="start-btn secondary"
            onClick={onClose}
            disabled={starting}
          >
            取消
          </button>
          <button
            type="button"
            className="start-btn"
            disabled={starting || !selectedAgent || workDeleted}
            onClick={() => onStart(selectedAgent!.agent_id, selectedBatchId || null)}
          >
            {starting ? "启动中…" : "开会话"}
          </button>
        </div>
      </div>

      {/* 二级对话框：新建 Agent */}
      {creating && (
        <div className="modal-overlay nested" onClick={() => !submitting && setCreating(false)}>
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
    </div>
  );
}
