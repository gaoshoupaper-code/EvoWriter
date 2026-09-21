/**
 * 文件下载（REQ-20260921-135543 FR-009）。
 *
 * 纯 web Blob 下载：WebView2 走原生下载流程（含另存为），
 * 不触碰 Tauri Rust 侧（避免与并行版本发布改动叠加 Cargo 文件）。
 */
export function downloadTextFile(filename: string, content: string, mime = "text/markdown;charset=utf-8") {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
