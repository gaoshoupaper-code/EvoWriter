import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import {
  UiFontSizeControl,
  applyUiFontSize,
  loadUiFontSize,
} from "../components/ui/UiFontSizeControl";

/**
 * AC-005:界面字号设置联动且持久。
 * FR-007:切换只改 --ui-font-size;非法/缺失存储回落默认 14。
 */
describe("UiFontSizeControl", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.style.removeProperty("--ui-font-size");
  });

  it("无存储时默认 14", () => {
    expect(loadUiFontSize()).toBe("14");
  });

  it("非法存储值回落 14(失败语义)", () => {
    window.localStorage.setItem("writer-ui-font-size", "99");
    expect(loadUiFontSize()).toBe("14");
  });

  it("applyUiFontSize 写入根变量", () => {
    applyUiFontSize("16");
    expect(
      document.documentElement.style.getPropertyValue("--ui-font-size"),
    ).toBe("16px");
  });

  it("点击「大」:变量、状态、持久化三联动", async () => {
    render(<UiFontSizeControl />);
    await userEvent.click(screen.getByRole("button", { name: "大" }));
    expect(
      document.documentElement.style.getPropertyValue("--ui-font-size"),
    ).toBe("16px");
    expect(window.localStorage.getItem("writer-ui-font-size")).toBe("16");
    expect(loadUiFontSize()).toBe("16");
  });

  it("点击「小」切到 13px,激活态跟随", async () => {
    render(<UiFontSizeControl />);
    const small = screen.getByRole("button", { name: "小" });
    await userEvent.click(small);
    expect(
      document.documentElement.style.getPropertyValue("--ui-font-size"),
    ).toBe("13px");
    expect(small.getAttribute("aria-pressed")).toBe("true");
  });
});
