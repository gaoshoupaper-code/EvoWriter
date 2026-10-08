import { useState } from "react";

/**
 * 界面字号控制(规范见 DESIGN-UI.md):
 * 只改 --ui-font-size 单一变量,全界面文字联动;图标/间距/圆角等几何不随字号缩放。
 * 选择持久化到 localStorage;存储不可用时回落默认 14px。
 * 注意:本文件与 evolution/desktop 同名文件保持同构(两端同步义务)。
 */

export type UiFontSize = "13" | "14" | "16";

const STORAGE_KEY = "writer-ui-font-size";

const OPTIONS: ReadonlyArray<{ value: UiFontSize; label: string }> = [
  { value: "13", label: "小" },
  { value: "14", label: "中" },
  { value: "16", label: "大" },
];

export function applyUiFontSize(size: UiFontSize): void {
  document.documentElement.style.setProperty("--ui-font-size", `${size}px`);
}

export function loadUiFontSize(): UiFontSize {
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    return v === "13" || v === "16" ? v : "14";
  } catch {
    return "14";
  }
}

function persistUiFontSize(size: UiFontSize): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, size);
  } catch {
    // 持久化失败不阻塞当次生效;重启后回落默认
  }
}

export function UiFontSizeControl() {
  const [size, setSize] = useState<UiFontSize>(() => loadUiFontSize());

  return (
    <div className="ui-font-size-control" role="group" aria-label="界面字号">
      <span className="ui-font-size-label">字号</span>
      <div className="ui-font-size-options">
        {OPTIONS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            className={`ui-font-size-option${size === opt.value ? " active" : ""}`}
            aria-pressed={size === opt.value}
            onClick={() => {
              setSize(opt.value);
              applyUiFontSize(opt.value);
              persistUiFontSize(opt.value);
            }}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  );
}
