import { useCallback, useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
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
import { DEFAULT_JUDGE_REPEATS } from "@/lib/experiment-types";
import { currentTask, pageAgent } from "@/lib/writing-agent";
import "@/components/bench/evaluation-readability.css";

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
  scoreRepeats: number;
}

const EMPTY_FORM: GroupFormState = { editingId: null, name: "", memberIds: [], scoreRepeats: DEFAULT_JUDGE_REPEATS };

const MIN_MEMBERS = 1;
const MAX_MEMBERS = 5;

export default function JudgeGroupPage() {
  const { search, hash, state } = useLocation();
  const query = new URLSearchParams(search);
  query.set("tab", "judge-groups");
  query.set("agent", pageAgent("models", search));
  return <Navigate to={`/bench/rules?${query}${hash}`} replace state={state} />;
}

export function JudgeGroupPanel() {
  const chapterText = currentTask() === "chapter_text";
  const [groups, setGroups] = useState<JudgeGroupInfo[]>([]);
  const [candidates, setCandidates] = useState<JudgeCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
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
      setLoadError("");
    } catch (err) {
      setLoadError(err instanceof Error ? `读取评测组失败：${err.message}` : "读取评测组失败");
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
      const payload = { name: form.name.trim(), member_config_ids: form.memberIds,
        ...(chapterText ? { score_repeats: form.scoreRepeats } : {}) };
      if (form.editingId === null) {
        await createJudgeGroup(payload);
        toast.success("评测组已创建");
      } else {
        await updateJudgeGroup(form.editingId, payload);
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

  if (loading) return <div className="page-loading" role="status">加载评测组…</div>;

  const byId = new Map(candidates.map((c) => [c.config_id, c]));

  return (
    <section className="config-page evaluation-content evaluation-judge-groups">
      <header className="page-header evaluation-judge-header">
        <div><h2>评测组</h2>
          <p className="page-desc">
            {chapterText ? "每组选择 1～5 个评测模型。各模型按相同次数评同一份成稿，评齐后汇总。" :
              "由 1~5 个 judge 模型组成的评审团：每行评分按维度取成员均分（1 位小数），同维成功 ≥2 才出分。1 人组等价于旧的单 judge 评测。"}
          </p>
        </div>
        <button type="button" className="config-button readable-button" onClick={() => setForm({ ...EMPTY_FORM })} disabled={!!form}>
          + 新建评测组
        </button>
      </header>
      {loadError && <p className="experiment-error" role="alert">{loadError}</p>}

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
          {chapterText && <label className="config-field">
            <span className="config-label">每个模型评分次数</span>
            <select className="config-input" value={form.scoreRepeats} disabled={saving}
              onChange={(event) => setForm({ ...form, scoreRepeats: Number(event.target.value) })}>
              {[1, 2, 3].map((times) => <option key={times} value={times}>{times} 次</option>)}
            </select>
          </label>}
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
            <button className="config-button primary readable-button" onClick={handleSave} disabled={saving}>
              {saving ? "保存中…" : "保存"}
            </button>
            <button className="config-button readable-button" onClick={() => setForm(null)} disabled={saving}>
              取消
            </button>
          </div>
        </section>
      )}

      <section className="config-list">
        {groups.length === 0 ? !loadError && (
          <div className="config-empty">
            还没有评测组。点击「+ 新建评测组」，选 1~5 个 judge 模型组队。
          </div>
        ) : (
          groups.map((g) => (
            <article key={g.id} className="config-card">
              <div className="config-card-head">
                <h3 className="config-card-name">{g.name}</h3>
                <span className="status-meta">{g.members.length} 人</span>
                {chapterText && <span className="status-meta">每个模型评 {g.score_repeats ?? DEFAULT_JUDGE_REPEATS} 次</span>}
              </div>
              <ul className="config-card-meta" aria-label="组内模型">
                {g.members.map((m) => {
                  const info = byId.get(m.config_id);
                  const model = info?.model || m.model;
                  const sameFamily = info?.same_family_as_executor ?? false;
                  return (
                    <li
                      key={m.config_id}
                      className="config-card-key"
                      title={sameFamily ? "与 executor 被测模型同家族，评测存在自我偏好风险（arXiv:2502.01534）" : undefined}
                    >
                      {m.name}（{model}）{m.stale && <span className="config-key-missing"> · 配置缺失</span>}
                      {sameFamily && <span className="config-key-missing"> · ⚠ 同家族</span>}
                    </li>
                  );
                })}
              </ul>
              <div className="config-card-actions">
                <button
                  className="config-button readable-button"
                  onClick={() =>
                    setForm({ editingId: g.id, name: g.name, memberIds: g.members.map((m) => m.config_id),
                      scoreRepeats: g.score_repeats ?? DEFAULT_JUDGE_REPEATS })
                  }
                >
                  编辑
                </button>
                <button className="config-button readable-button danger" onClick={() => handleDelete(g)}>
                  删除
                </button>
              </div>
            </article>
          ))
        )}
      </section>
    </section>
  );
}
