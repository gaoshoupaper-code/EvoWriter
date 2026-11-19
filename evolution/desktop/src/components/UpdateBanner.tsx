import { useEffect, useState, useSyncExternalStore } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { toast } from "sonner";

/**
 * 更新提示横条（2026-07-08 新增）。
 *
 * 首次挂载时检查更新，并接收用户在设置页主动检查的结果。
 * 用户点「更新内容 ▾」可展开看本次版本的更新条目（changelog，来自 latest-evo.json 的 notes）；
 * 用户点「立即更新」后下载 + 验签 + 安装 + 重启。
 *
 * 检查失败静默——更新是锦上添花，不打扰用户。
 */

/** Rust updater::UpdateInfo 的前端镜像（字段对齐，可选字段用 null 兜底）。 */
interface UpdateInfo {
  available: boolean;
  status: string;
  current_version: string;
  version: string | null;
  date: string | null;
  body: string | null;
}

type InstallProgress =
  | { phase: "connecting" | "retrying" | "installing" }
  | { phase: "downloading"; downloaded: number; total: number | null };

// 同一次安装会跨越路由切换。重挂提示条时接回原下载。
let currentProgress: InstallProgress | null = null;
const listeners = new Set<() => void>();
const getProgress = () => currentProgress;
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
function setProgress(value: InstallProgress | null) {
  currentProgress = value;
  for (const listener of listeners) listener();
}
let startupCheck: Promise<UpdateInfo> | undefined;

function updateLabel(progress: InstallProgress | null) {
  if (!progress) return "立即更新";
  if (progress.phase === "downloading") {
    const downloaded = (progress.downloaded / 1_000_000).toFixed(1);
    if (!progress.total) return `已下载 ${downloaded} MB`;
    const total = (progress.total / 1_000_000).toFixed(1);
    const percent = Math.min(100, Math.floor(progress.downloaded * 100 / progress.total));
    return `已下载 ${downloaded} / ${total} MB（${percent}%）`;
  }
  if (progress.phase === "retrying") return "正在切换为普通下载…";
  return progress.phase === "connecting" ? "正在连接下载…" : "正在验证并安装…";
}

/**
 * 解析 notes（body 字段）为更新条目列表。
 * 发布脚本把 changelog 写成 JSON 数组字符串（如 '["修复A","新增B"]'），
 * 兼容旧版的纯文本（按换行拆分）和空值。
 */
function parseChangelog(body: string | null): string[] {
  if (!body) return [];
  // 优先尝试 JSON 数组解析（新版发布脚本格式）
  try {
    const parsed = JSON.parse(body);
    if (Array.isArray(parsed) && parsed.every((x) => typeof x === "string")) {
      return parsed;
    }
  } catch {
    // 不是 JSON，按纯文本处理
  }
  // 兼容旧版/纯文本：按换行拆分，去空行去重
  return body
    .split("\n")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

export default function UpdateBanner() {
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const progress = useSyncExternalStore(subscribe, getProgress);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let mounted = true;
    const subscription = listen<UpdateInfo>("update_available", ({ payload }) => {
      if (mounted && payload.available) {
        startupCheck = Promise.resolve(payload);
        setInfo(payload);
      }
    }).catch(() => undefined);
    startupCheck ??= invoke<UpdateInfo>("check_update");
    startupCheck.then((result) => {
      if (mounted && result.available) setInfo(result);
    }).catch(() => { startupCheck = undefined; });

    return () => {
      mounted = false;
      void subscription.then(unlisten => unlisten?.());
    };
  }, []);

  async function handleInstall() {
    if (currentProgress) return;
    if (!confirm("确认现在下载并安装更新？安装完成后应用将自动重启。")) return;
    setProgress({ phase: "connecting" });
    let unlisten: UnlistenFn | undefined;
    try {
      unlisten = await listen<InstallProgress>("update_progress", ({ payload }) => setProgress(payload));
      await invoke("install_update");
      // install_update 内部会 app.restart()，这行正常情况执行不到
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err || "更新安装失败"));
    } finally {
      unlisten?.();
      setProgress(null);
    }
  }

  if (!info?.available) return null;

  const changelog = parseChangelog(info.body);
  const hasChangelog = changelog.length > 0;

  return (
    <div className="update-banner">
      <div className="update-banner-main">
        <span className="update-banner-text">
          ✨ 发现新版本 v{info.version}
          <span className="update-banner-note">（当前 v{info.current_version}）</span>
        </span>
        {hasChangelog && (
          <button
            className="update-banner-toggle"
            onClick={() => setExpanded((v) => !v)}
          >
            更新内容 {expanded ? "▴" : "▾"}
          </button>
        )}
      </div>
      <button
        className="update-banner-btn"
        onClick={handleInstall}
        disabled={progress !== null}
      >
        {updateLabel(progress)}
      </button>
      {expanded && hasChangelog && (
        <ul className="update-banner-changelog">
          {changelog.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
