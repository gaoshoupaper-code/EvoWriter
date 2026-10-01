/**
 * 卸载全部 testing-library 挂载点的辅助（重启模拟测试用）。
 * RTL 的 cleanup 也会在测试结束自动跑，但「同测试内两次挂载」需要手动卸载第一次。
 */
import { cleanup, render } from "@testing-library/react";

export { render };
export function unmountAll() {
  cleanup();
}
