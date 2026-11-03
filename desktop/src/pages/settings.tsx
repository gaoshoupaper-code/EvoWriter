import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { invoke } from "@tauri-apps/api/core";
import { toast } from "sonner";
import { fetchMeOrNull } from "@/lib/api";
import { BillingPanel } from "@/components/billing/BillingPanel";
import { UiFontSizeControl } from "@/components/ui/UiFontSizeControl";
import { useBillingStore } from "@/stores/billing";

export default function SettingsPage() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [serverUrl, setServerUrl] = useState("");
  const [checkingUpdate, setCheckingUpdate] = useState(false);

  useEffect(() => {
    let ignore = false;
    void (async () => {
      const me = await fetchMeOrNull();
      if (ignore) return;
      if (!me) { navigate("/login", { replace: true }); return; }
      useBillingStore.getState().setOwner(me.username);
      try {
        const url = await invoke<string>("get_server_url");
        if (!ignore) setServerUrl(url);
      } catch {
        if (!ignore) setServerUrl("https://siyen.site");
      }
      if (!ignore) setLoading(false);
    })();
    return () => { ignore = true; };
  }, [navigate]);

  async function handleCheckUpdate() {
    if (checkingUpdate) return;
    setCheckingUpdate(true);
    try {
      const info = await invoke<{ available: boolean; status: string; current_version: string; version: string | null; body: string | null }>("check_update");
      if (info.status === "update_available") toast.success(`发现新版本 v${info.version}，请看顶部提示更新`);
      else if (info.status === "check_failed") toast.error(info.body ?? "检查更新失败");
      else toast.success(`已是最新版本（v${info.current_version}）`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "检查更新失败");
    } finally { setCheckingUpdate(false); }
  }

  if (loading) return <main className="settings-page"><p className="settings-loading">加载中…</p></main>;
  return <main className="settings-page"><div className="settings-container">
    <header className="settings-header"><h1 className="settings-title">设置</h1><Link to="/" className="settings-back">返回工作区</Link></header>
    <BillingPanel />
    <section className="settings-card">
      <header className="settings-card-head"><div className="settings-card-title"><h2>通用</h2></div></header>
      <div className="settings-rows">
        <div className="settings-row"><div className="settings-row-info"><strong>服务器地址</strong><p>应用连接的云端服务，由系统统一分配，不可修改。</p></div><code className="settings-server-url" title={serverUrl}>{serverUrl || "加载中…"}</code></div>
        <div className="settings-row"><div className="settings-row-info"><strong>应用更新</strong><p>启动时自动检查；有新版本时顶部会出现提示条，点「立即更新」即可安装。</p></div><button className="settings-btn" type="button" onClick={() => void handleCheckUpdate()} disabled={checkingUpdate}>{checkingUpdate ? "检查中…" : "检查更新"}</button></div>
        <div className="settings-row"><div className="settings-row-info"><strong>界面字号</strong><p>调整整个界面的文字大小，立即生效；图标与间距保持不变，重启后记住选择。</p></div><UiFontSizeControl /></div>
      </div>
    </section>
  </div></main>;
}
