import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  UiFontSizeControl,
  applyUiFontSize,
  loadUiFontSize,
} from "../components/ui/UiFontSizeControl";

describe("UiFontSizeControl", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.style.removeProperty("--ui-font-size");
  });

  it("无存储时默认选中中档 14px", () => {
    expect(loadUiFontSize()).toBe("14");
    render(<UiFontSizeControl />);
    expect(screen.getByRole("button", { name: "中", pressed: true })).toBeVisible();
  });

  it("非法存储值回落中档", () => {
    window.localStorage.setItem("writer-ui-font-size", "99");
    expect(loadUiFontSize()).toBe("14");
  });

  it("读不到存储时回落中档", () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementationOnce(() => {
      throw new Error("Storage unavailable");
    });
    try {
      expect(loadUiFontSize()).toBe("14");
    } finally {
      getItem.mockRestore();
    }
  });

  it.each([["13", "小"], ["14", "中"], ["16", "大"]])("恢复手选字号 %s", (value, label) => {
    window.localStorage.setItem("writer-ui-font-size", value);
    expect(loadUiFontSize()).toBe(value);
    render(<UiFontSizeControl />);
    expect(screen.getByRole("button", { name: label, pressed: true })).toBeVisible();
  });

  it("applyUiFontSize 写入根变量", () => {
    applyUiFontSize("16");
    expect(
      document.documentElement.style.getPropertyValue("--ui-font-size"),
    ).toBe("16px");
  });

  it("点击「大」:变量、状态、持久化三联动", () => {
    render(<UiFontSizeControl />);
    fireEvent.click(screen.getByRole("button", { name: "大" }));
    expect(
      document.documentElement.style.getPropertyValue("--ui-font-size"),
    ).toBe("16px");
    expect(window.localStorage.getItem("writer-ui-font-size")).toBe("16");
    expect(loadUiFontSize()).toBe("16");
  });

  it("点击「小」切到 13px,激活态跟随", () => {
    render(<UiFontSizeControl />);
    const small = screen.getByRole("button", { name: "小" });
    fireEvent.click(small);
    expect(
      document.documentElement.style.getPropertyValue("--ui-font-size"),
    ).toBe("13px");
    expect(small.getAttribute("aria-pressed")).toBe("true");
    expect(window.localStorage.getItem("writer-ui-font-size")).toBe("13");
    expect(loadUiFontSize()).toBe("13");
  });
});
