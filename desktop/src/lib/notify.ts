/**
 * 桌面系统通知（REQ-20261019-232617 FR-001/DEC-002）。
 *
 * 段产物齐全时提醒作者「可以进入下一阶段」。走 Tauri notification 插件
 * （应用后台也收得到）；非 Tauri 环境（vitest jsdom / 浏览器）静默降级，
 * 不抛错——通知是提醒增强，失败不阻塞创作主流程（FR-001 失败语义）。
 */
export async function notifyStageReady(stageLabel: string, nextLabel: string): Promise<void> {
  try {
    const mod = await import("@tauri-apps/plugin-notification");
    let granted = await mod.isPermissionGranted();
    if (!granted) {
      granted = (await mod.requestPermission()) === "granted";
    }
    if (granted) {
      mod.sendNotification({
        title: `${stageLabel}已齐全`,
        body: `可以进入${nextLabel}了——回到应用点击「进入${nextLabel}」确认。`,
      });
    }
  } catch {
    // 非 Tauri 环境或插件不可用：静默降级（聊天流确认卡片仍是确认入口）
  }
}
