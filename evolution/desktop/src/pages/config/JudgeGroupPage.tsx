import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  listJudgeCandidates,
  listJudgeGroups,
  createJudgeGroup,
  updateJudgeGroup,
  deleteJudgeGroup,
  type JudgeCandidate,
  type JudgeGroupInfo,
} from "@/lib/api";

/**
 * 评测组配置页（REQ-20260930-162207/FR-001/DEC-001、004、010、011）。
 *
 * 组 = 1~5 个 judge 候选配置的命名组合，触发评测批次时整批使用（DEC-004：
 * 1 人组 = 单评特例）。成员仅限 eval/evolution scope（判评分离，DEC-010）；
 * 成员与 executor 被测模型同家族时逐个显示黄条告警（DEC-011，仅告警不阻断）。
 * 成员配置被删时后端联动剔除、组空自动删组（DEC-005）。
 */

/** 编辑表单状态：editingId=null=新建。 */
interface GroupFormState {
  editingId: number | null;
  name: string;
  memberIds: number[];
}

const EMPTY_FORM: GroupFormState = { editingId: null, name: "", memberIds: [] };

const MIN_MEMBERS = 1;
const MAX_MEMBERS = 5;

export default function JudgeGroupPage() {
  const [groups, setGroups] = useState<JudgeGroupInfo[]>([]);
  const [candidates, setCandidates] = useState<JudgeCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState<GroupFormState | null>(null);
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [groupResp, judgeResp] = await Promise.all([
        listJudgeGroups(),
        listJudgeCandidates(),
      ]);
      setGroups(groupResp.groups);
      setCandidates(judgeResp.judges);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "读取评测组失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  function toggleMember(configId: number) {
    setForm((f) => {
      if (!f) return f;
      const has = f.memberIds.includes(configId);
      if (!has && f.memberIds.length >= MAX_MEMBERS) {
        toast.error(`评测组最多 ${MAX_MEMBERS} 个成员（防成本失控）`);
        return f;
      }
      return {
        ...f,
        memberIds: has
          ? f.memberIds.filter((id) => id !== configId)
          : [...f.memberIds, configId],
      };
    });
  }

  async function handleSave() {
    if (!form) return;
    if (!form.name.trim()) {
      toast.error("组名不能为空");
      return;
    }
    if (form.memberIds.length < MIN_MEMBERS || form.memberIds.length > MAX_MEMBERS) {
      toast.error(`成员数须为 ${MIN_MEMBERS}~${MAX_MEMBERS} 个（当前 ${form.memberIds.length}）`);
      return;
    }
    setSaving(true);
    try {
      if (form.editingId === null) {
        await createJudgeGroup({ name: form.name.trim(), member_config_ids: form.memberIds });
        toast.success("评测组已创建");
      } else {
        await updateJudgeGroup(form.editingId, {
          name: form.name.trim(),
          member_config_ids: form.memberIds,
        });
        toast.success("评测组已更新");
      }
      setForm(null);
      await refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(group: JudgeGroupInfo) {
    if (!window.confirm(`确认删除评测组「${group.name}」？历史批次不受影响（快照独立保存）。`)) return;
    try {
      await deleteJudgeGroup(group.id);
      toast.success("已删除");
      await refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    }
  }

  if (loading) return <div className="page-loading">加载评测组…</div>;

  const byId = new Map(candidates.map((c) => [c.config_id, c]));

  return (
    <div className="config-page">
      <header className="page-header">
        <h1>评测组</h1>
        <p className="page-desc">
          由 1~5 个 judge 模型组成的评审团：每行评分按维度取成员均分（1 位小数），
          同维成功 ≥2 才出分。1 人组等价于旧的单 judge 评测。
        </p>
      </header>

      <div className="config-toolbar">
        <button
          className="config-button primary"
          onClick={() => setForm({ ...EMPTY_FORM })}
          disabled={!!form}
        >
          + 新建评测组
        </button>
      </div>

      {form && (
        <section className="config-form">
          <div className="config-form-title">
            {form.editingId === null ? "新建评测组" : "编辑评测组"}
          </div>
          <label className="config-field">
            <span className="config-label">组名</span>
            <input
              className="config-input"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="如 三人评审团"
              disabled={saving}
            />
          </label>
          <div className="config-field">
            <span className="config-label">
              成员（{form.memberIds.length}/{MAX_MEMBERS}，至少 {MIN_MEMBERS} 个）
            </span>
            <div className="bench-case-picker-body">
              {candidates.length === 0 ? (
                <div className="config-empty">
                  无可用 judge 候选——先到「进化端模型」页配置（eval 或 evolution scope）。
                </div>
              ) : (
                candidates.map((c) => (
                  <label key={c.config_id} className="bench-case-picker-item">
                    <input
                      type="checkbox"
                      checked={form.memberIds.includes(c.config_id)}
                      onChange={() => toggleMember(c.config_id)}
                      disabled={saving || (!c.has_key && !form.memberIds.includes(c.config_id))}
                    />
                    <span>{c.name}（{c.model} · {c.scope}）</span>
                    {!c.has_key && <span className="config-key-missing">缺 key 不可用</span>}
                    {c.same_family_as_executor && (
                      <span className="config-key-missing" title="与 executor 被测模型同家族，评测存在自我偏好风险">
                        ⚠ 同家族
                      </span>
                    )}
                  </label>
                ))
              )}
            </div>
          </div>
          <div className="config-actions">
            <button className="config-button primary" onClick={handleSave} disabled={saving}>
              {saving ? "保存中…" : "保存"}
            </button>
            <button className="config-button ghost" onClick={() => setForm(null)} disabled={saving}>
              取消
            </button>
          </div>
        </section>
      )}

      <section className="config-list">
        {groups.length === 0 ? (
          <div className="config-empty">
            还没有评测组。点击「+ 新建评测组」，选 1~5 个 judge 模型组队。
          </div>
        ) : (
          groups.map((g) => (
            <div key={g.id} className="config-card">
              <div className="config-card-head">
                <span className="config-card-name">{g.name}</span>
                <span className="status-meta">{g.members.length} 人</span>
              </div>
              <div className="config-card-meta">
                {g.members.map((m) => {
                  const info = byId.get(m.config_id);
                  const model = info?.model || m.model;
                  const sameFamily = info?.same_family_as_executor ?? false;
                  return (
                    <span
                      key={m.config_id}
                      className="config-card-key"
                      title={sameFamily ? "与 executor 被测模型同家族，评测存在自我偏好风险（arXiv:2502.01534）" : undefined}
                    >
                      {m.name}（{model}）{m.stale && <span className="config-key-missing"> · 配置缺失</span>}
                      {sameFamily && <span className="config-key-missing"> · ⚠ 同家族</span>}
                    </span>
                  );
                })}
              </div>
              <div className="config-card-actions">
                <button
                  className="config-button small"
                  onClick={() =>
                    setForm({ editingId: g.id, name: g.name, memberIds: g.members.map((m) => m.config_id) })
                  }
                >
                  编辑
                </button>
                <button className="config-button small danger" onClick={() => handleDelete(g)}>
                  删除
                </button>
              </div>
            </div>
          ))
        )}
      </section>
    </div>
  );
}
