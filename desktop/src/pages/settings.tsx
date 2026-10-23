
import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Link } from "react-router-dom";
import { invoke } from "@tauri-apps/api/core";
import { toast } from "sonner";
import {
  activateProviderConfig,
  clearApiKey,
  createProviderConfig,
  deleteProviderConfig,
  fetchMeOrNull,
  listProviderConfigs,
  updateProviderConfig,
  type ProviderConfig,
} from "@/lib/api";
import { UiFontSizeControl } from "@/components/ui/UiFontSizeControl";
import { WritingSkillsCard } from "@/components/settings/WritingSkillsCard";

type FormMode =
  | { kind: "new" }
  | { kind: "edit"; config: ProviderConfig };

export default function SettingsPage() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [configs, setConfigs] = useState<ProviderConfig[]>([]);
  const [activeConfigId, setActiveConfigId] = useState<string | null>(null);
  const [mode, setMode] = useState<FormMode>({ kind: "new" });
  const [submitting, setSubmitting] = useState(false);

  // 表单字段
  const [name, setName] = useState("");
  const [apiKey, setApiKeyInput] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [activate, setActivate] = useState(true);

  // 服务器地址：只读展示（由系统统一分配，不提供修改入口）
  const [serverUrl, setServerUrl] = useState("");

  // 应用更新（手动检查；启动时 Rust 已自动检查一次并 emit 顶部提示条）
  const [checkingUpdate, setCheckingUpdate] = useState(false);

  async function reload() {
    try {
      const list = await listProviderConfigs();
      setConfigs(list);
      const active = list.find((c) => c.is_active);
      setActiveConfigId(active?.config_id ?? null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "加载设置失败。");
    }
    // 加载服务器地址（独立于 API profile，来自 Rust Store）
    try {
      setServerUrl(await invoke<string>("get_server_url"));
    } catch {
      // Store 读取失败用默认值
      setServerUrl("https://siyen.site");
    }
  }

  /// 手动检查更新：调 Rust check_update，凭 status 精确区分三种结果。
  async function handleCheckUpdate() {
    if (checkingUpdate) return;
    setCheckingUpdate(true);
    try {
      const info = await invoke<{
        available: boolean;
        status: string;
        current_version: string;
        version: string | null;
        body: string | null;
      }>("check_update");
      // 不再只看 available——status 精确区分「已是最新」与「检查失败」，避免谎报。
      if (info.status === "update_available") {
        toast.success(`发现新版本 v${info.version}，请看顶部提示更新`);
      } else if (info.status === "check_failed") {
        toast.error(info.body ?? "检查更新失败");
      } else {
        toast.success(`已是最新版本（v${info.current_version}）`);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "检查更新失败");
    } finally {
      setCheckingUpdate(false);
    }
  }

  useEffect(() => {
    let ignore = false;
    (async () => {
      const me = await fetchMeOrNull();
      if (ignore) return;
      if (!me) {
        navigate("/login", { replace: true });
        return;
      }
      await reload();
      if (!ignore) setLoading(false);
    })();
    return () => { ignore = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [navigate]);

  function resetForm() {
    setMode({ kind: "new" });
    setName("");
    setApiKeyInput("");
    setBaseUrl("");
    setModel("");
    setActivate(true);
  }

  function startEdit(c: ProviderConfig) {
    setMode({ kind: "edit", config: c });
    setName(c.name);
    setApiKeyInput(""); // 编辑时不回显 key（安全），留空表示不改
    setBaseUrl(c.base_url ?? "");
    setModel(c.model);
    setActivate(c.is_active);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    if (!name.trim() || !model.trim()) {
      toast.error("名称和模型不能为空。");
      return;
    }
    if (mode.kind === "new" && !apiKey.trim()) {
      toast.error("新建配置时 API Key 不能为空。");
      return;
    }
    setSubmitting(true);
    try {
      if (mode.kind === "new") {
        await createProviderConfig({
          name: name.trim(),
          api_key: apiKey.trim(),
          base_url: baseUrl.trim() || null,
          model: model.trim(),
          activate,
        });
        toast.success("配置已保存。");
      } else {
        const patch: Record<string, unknown> = {
          name: name.trim(),
          base_url: baseUrl.trim() || null,
          model: model.trim(),
        };
        if (apiKey.trim()) patch.api_key = apiKey.trim();
        await updateProviderConfig(mode.config.config_id, patch);
        if (activate && !mode.config.is_active) {
          await activateProviderConfig(mode.config.config_id);
        }
        toast.success("配置已更新。");
      }
      resetForm();
      await reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败。");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleActivate(configId: string) {
    if (submitting) return;
    setSubmitting(true);
    try {
      await activateProviderConfig(configId);
      toast.success("已切换为当前使用配置。");
      await reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "切换失败。");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(configId: string, configName: string) {
    if (!confirm(`确定删除配置「${configName}」？此操作不可撤销。`)) return;
    if (submitting) return;
    setSubmitting(true);
    try {
      await deleteProviderConfig(configId);
      toast.success("已删除。");
      if (mode.kind === "edit" && mode.config.config_id === configId) resetForm();
      await reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败。");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleClearActive() {
    if (!activeConfigId) return;
    if (!confirm("清除当前激活的配置？清除后 AI 功能不可用，直到重新激活。")) return;
    setSubmitting(true);
    try {
      await clearApiKey();
      toast.success("已清除当前激活配置。");
      await reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "清除失败。");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <main className="settings-page">
        <p className="settings-loading">加载中…</p>
      </main>
    );
  }

  const hasActive = activeConfigId !== null;
  const activeConfig = configs.find((c) => c.config_id === activeConfigId) ?? null;

  return (
    <main className="settings-page">
      <div className="settings-container">
        <header className="settings-header">
          <h1 className="settings-title">设置</h1>
          <Link to="/" className="settings-back">返回工作区</Link>
        </header>

        {/* 模型服务：多套 API 配置管理与切换 */}
        <section className="settings-card">
          <header className="settings-card-head">
            <div className="settings-card-title">
              <h2>模型服务</h2>
              <p>保存多套 API 配置，随时切换当前使用的模型。</p>
            </div>
            <span className={`settings-status ${hasActive ? "ok" : "missing"}`}>
              <span className="settings-status-text">
                {hasActive && activeConfig
                  ? `使用中：${activeConfig.name} · ${activeConfig.model}`
                  : "未配置，AI 功能不可用"}
              </span>
            </span>
          </header>

          <div className="provider-layout">
            {/* 左侧：配置列表 */}
            <aside className="provider-list">
              <div className="provider-list-head">
                <span>我的配置（{configs.length}）</span>
                <button type="button" className="provider-new-btn" onClick={resetForm}>+ 新建</button>
              </div>
              {configs.length === 0 ? (
                <p className="provider-empty">还没有配置，点「新建」添加。</p>
              ) : (
                configs.map((c) => (
                  <div
                    key={c.config_id}
                    className={`provider-item ${c.config_id === activeConfigId ? "active" : ""} ${mode.kind === "edit" && mode.config.config_id === c.config_id ? "editing" : ""}`}
                  >
                    <div className="provider-item-main" onClick={() => startEdit(c)}>
                      <div className="provider-item-name">
                        {c.name}
                        {c.is_active ? <span className="provider-badge">使用中</span> : null}
                      </div>
                      <div className="provider-item-meta" title={c.base_url || undefined}>
                        {c.model} · {c.base_url || "默认地址"}
                      </div>
                    </div>
                    <div className="provider-item-actions">
                      {!c.is_active ? (
                        <button type="button" className="provider-action" onClick={() => handleActivate(c.config_id)}>切换</button>
                      ) : null}
                      <button type="button" className="provider-action danger" onClick={() => handleDelete(c.config_id, c.name)}>删除</button>
                    </div>
                  </div>
                ))
              )}
              {hasActive ? (
                <button type="button" className="provider-clear-active" onClick={handleClearActive}>
                  清除当前激活
                </button>
              ) : null}
            </aside>

            {/* 右侧：表单 */}
            <div className="provider-form">
              <h3>{mode.kind === "new" ? "新建配置" : `编辑「${mode.config.name}」`}</h3>
              <form className="auth-form" onSubmit={handleSubmit}>
                <label className="auth-field">
                  <span>名称（方便区分，如「我的GLM」）</span>
                  <input
                    className="auth-input"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="给这套配置起个名字"
                    disabled={submitting}
                  />
                </label>
                <label className="auth-field">
                  <span>API Key {mode.kind === "edit" ? "（留空不修改）" : ""}</span>
                  <input
                    className="auth-input"
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKeyInput(e.target.value)}
                    placeholder={mode.kind === "edit" ? "••••••（已加密保存）" : "sk-..."}
                    autoComplete="off"
                    disabled={submitting}
                  />
                </label>
                <label className="auth-field">
                  <span>Base URL（可选）</span>
                  <input
                    className="auth-input"
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    placeholder="https://open.bigmodel.cn/api/paas/v4"
                    autoComplete="off"
                    disabled={submitting}
                  />
                </label>
                <label className="auth-field">
                  <span>模型名称</span>
                  <input
                    className="auth-input"
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    placeholder="glm-4.6 / deepseek-chat / gpt-4o 等"
                    autoComplete="off"
                    disabled={submitting}
                  />
                </label>
                <label className="auth-checkbox">
                  <input
                    type="checkbox"
                    checked={activate}
                    onChange={(e) => setActivate(e.target.checked)}
                    disabled={submitting}
                  />
                  <span>保存后立即设为当前使用</span>
                </label>
                <div className="settings-form-actions">
                  {mode.kind === "edit" ? (
                    <button className="settings-btn" type="button" onClick={resetForm} disabled={submitting}>
                      取消编辑
                    </button>
                  ) : null}
                  <button className="settings-btn settings-btn-primary" type="submit" disabled={submitting}>
                    {submitting ? "保存中…" : mode.kind === "new" ? "保存配置" : "更新配置"}
                  </button>
                </div>
              </form>
              <p className="settings-hint">
                平台用 AES-256-GCM 加密存储你的 Key。列表只显示名称/模型，不显示 Key 明文。
              </p>
            </div>
          </div>
        </section>

        {/* 写作技能：用户自定义技能库（REQ-20261023-000000） */}
        <WritingSkillsCard />

        {/* 通用：服务器地址（只读）+ 应用更新 + 界面字号 */}
        <section className="settings-card">
          <header className="settings-card-head">
            <div className="settings-card-title">
              <h2>通用</h2>
            </div>
          </header>
          <div className="settings-rows">
            <div className="settings-row">
              <div className="settings-row-info">
                <strong>服务器地址</strong>
                <p>应用连接的云端服务，由系统统一分配，不可修改。</p>
              </div>
              <code className="settings-server-url" title={serverUrl}>
                {serverUrl || "加载中…"}
              </code>
            </div>
            <div className="settings-row">
              <div className="settings-row-info">
                <strong>应用更新</strong>
                <p>启动时自动检查；有新版本时顶部会出现提示条，点「立即更新」即可安装。</p>
              </div>
              <button
                className="settings-btn"
                type="button"
                onClick={handleCheckUpdate}
                disabled={checkingUpdate}
              >
                {checkingUpdate ? "检查中…" : "检查更新"}
              </button>
            </div>
            <div className="settings-row">
              <div className="settings-row-info">
                <strong>界面字号</strong>
                <p>调整整个界面的文字大小，立即生效；图标与间距保持不变，重启后记住选择。</p>
              </div>
              <UiFontSizeControl />
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
