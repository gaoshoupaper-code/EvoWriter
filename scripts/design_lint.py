#!/usr/bin/env python3
"""design_lint.py — Writer 两端桌面设计规范扫描(DESIGN-UI.md 的执行器)。

规则覆盖 .css 与 .tsx:
  1. 字号硬编码(font-size / fontSize 非 var(--text-ui-*))
  2. 字体栈硬编码(font-family / fontFamily 非 var;@font-face 块豁免)
  3. 散落圆角(border-radius 数值;999px/50%/0/var 豁免)
  4. 动效时长硬编码(transition 中出现数值时长,须 var(--motion-*))
  5. 非法阴影(box-shadow 须 var(--shadow-*);普通面板应无阴影)
  6. 两端 globals.css 设计 token 区一致性校验

豁免:违规行上一行或行尾注释含 `design-lint:allow`(可附原因)。

用法:
  python scripts/design_lint.py           # 输出违规清单
  python scripts/design_lint.py --check   # 违规时退出码 1
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APPS = ["desktop/src", "evolution/desktop/src"]

ALLOW_MARK = "design-lint:allow"

# ---------------------------------------------------------------- 基础工具

def strip_comment(line: str) -> str:
    return line.split("/*", 1)[0].split("//", 1)[0]


def line_allowed(lines: list[str], idx: int) -> bool:
    """行尾或上一行带豁免注释即豁免。"""
    if ALLOW_MARK in lines[idx]:
        return True
    if idx > 0 and ALLOW_MARK in lines[idx - 1]:
        return True
    return False


# ---------------------------------------------------------------- CSS 扫描

NUM_DURATION = re.compile(r"\b\d+(?:\.\d+)?m?s\b")


def scan_css(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    issues: list[str] = []

    in_font_face = False
    in_keyframes = False
    brace_depth = 0

    # 多行 transition 以分号收束:记录未闭合的 transition 起始行
    open_transition_row = None

    for i, raw in enumerate(lines, 1):
        line = raw
        allowed = line_allowed(lines, i - 1)

        # @font-face / @keyframes 块跟踪
        if "@font-face" in strip_comment(line):
            in_font_face = True
        if "@keyframes" in strip_comment(line):
            in_keyframes = True
        opens = strip_comment(line).count("{")
        closes = strip_comment(line).count("}")
        if opens:
            brace_depth += opens
        if closes:
            brace_depth -= closes
            if in_font_face and brace_depth <= 0:
                in_font_face = False
            if in_keyframes and brace_depth <= 0:
                in_keyframes = False

        code = strip_comment(line)

        # 规则 1:字号
        m = re.search(r"font-size:\s*([^;]+)", code)
        if m and not in_font_face and not allowed:
            val = m.group(1).strip()
            if not val.startswith("var("):
                issues.append(f"{i}: font-size 硬编码: {val.strip()}")

        # 规则 2:字体栈
        m = re.search(r"font-family:\s*([^;]+)", code)
        if m and not in_font_face and not allowed:
            val = m.group(1).strip()
            if val != "inherit" and not val.startswith("var("):
                issues.append(f"{i}: font-family 硬编码: {val[:60]}")

        # 规则 3:散落圆角
        m = re.search(r"border-radius:\s*([^;]+)", code)
        if m and not allowed:
            val = m.group(1).strip().rstrip(";")
            parts = val.split()
            ok = all(
                q.startswith("var(") or q in ("0", "0px", "50%", "999px", "9999px", "inherit", "none")
                for q in parts
            )
            if not ok:
                issues.append(f"{i}: border-radius 散落值: {val}")

        # 规则 4:transition 时长(跨行以分号收束)
        if re.search(r"transition\s*:", code) and open_transition_row is None:
            if ";" in code.split("transition", 1)[1]:
                seg = code.split(":", 1)[1]
                _check_transition(seg, i, allowed, issues)
            else:
                open_transition_row = i
                open_allowed = allowed
        elif open_transition_row is not None:
            if ";" in code or i - open_transition_row > 8:
                seg = " ".join(lines[open_transition_row - 1 : i])
                _check_transition(seg, open_transition_row, open_allowed, issues)
                open_transition_row = None

        # 规则 5:阴影(none 与 keyframes 动画帧合法;值须 var(--shadow-*))
        m = re.search(r"box-shadow:\s*([^;]+)", code)
        if m and not in_keyframes and not allowed:
            val = m.group(1).strip()
            if val != "none" and not val.startswith("var(--shadow"):
                issues.append(f"{i}: box-shadow 须 var(--shadow-*): {val[:60]}")

    return issues


def _check_transition(segment: str, row: int, allowed: bool, issues: list[str]) -> None:
    if allowed:
        return
    if re.search(r"transition\s*:\s*none", segment):
        return
    if NUM_DURATION.search(segment) and "var(" not in segment:
        issues.append(
            f"{row}: transition 数值时长(须 var(--motion-duration)): "
            + re.sub(r"\s+", " ", segment).strip()[:70]
        )


# ---------------------------------------------------------------- TSX 扫描

def scan_tsx(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    issues: list[str] = []

    for i, raw in enumerate(lines, 1):
        allowed = line_allowed(lines, i - 1)
        if allowed:
            continue
        code = strip_comment(raw)

        m = re.search(r"fontSize:\s*(\"[^\"]*\"|[\d.]+)", code)
        if m:
            val = m.group(1)
            if not (val.startswith('"') and val.startswith('"var(')):
                issues.append(f"{i}: fontSize 硬编码: {val}")

        m = re.search(r"fontFamily:\s*\"([^\"]*)\"", code)
        if m and not m.group(1).startswith("var("):
            issues.append(f"{i}: fontFamily 硬编码: {m.group(1)[:60]}")

        m = re.search(r"borderRadius:\s*[\d.]+", code)
        if m:
            issues.append(f"{i}: borderRadius 数值: {m.group(0)}")

        m = re.search(r"boxShadow:\s*\"", code)
        if m:
            issues.append(f"{i}: boxShadow 内联值")

    return issues


# ---------------------------------------------------------------- token 一致性

TOKEN_KEYS = (
    "--ui-font-size",
    "--text-ui-",
    "--font-mono",
    "--motion-",
)


def extract_tokens(path: Path) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*(--[a-z0-9-]+)\s*:\s*([^;]+);", line)
        if not m:
            continue
        name, val = m.group(1), m.group(2).strip()
        if name.startswith(TOKEN_KEYS):
            tokens[name] = val
    return tokens


def check_token_parity() -> list[str]:
    issues: list[str] = []
    sets: dict[str, dict[str, str]] = {}
    for app in APPS:
        css = REPO / app / "styles" / "globals.css"
        if not css.exists():
            issues.append(f"缺少 {css}")
            continue
        sets[app] = extract_tokens(css)
    if len(sets) == 2:
        a, b = sets[APPS[0]], sets[APPS[1]]
        for key in sorted(set(a) | set(b)):
            if key not in a:
                issues.append(f"token 仅存在于 {APPS[1]}: {key}")
            elif key not in b:
                issues.append(f"token 仅存在于 {APPS[0]}: {key}")
            elif a[key] != b[key]:
                issues.append(f"token 两端不一致: {key}: {a[key]!r} != {b[key]!r}")
    return issues


# ---------------------------------------------------------------- 主流程

def main() -> int:
    check_only = "--check" in sys.argv
    total = 0
    for app in APPS:
        root = REPO / app
        css_files = sorted(root.rglob("*.css"))
        tsx_files = sorted(root.rglob("*.tsx"))
        tsx_files = [p for p in tsx_files if not p.name.endswith((".test.tsx", ".spec.tsx"))]

        for path in css_files:
            issues = scan_css(path)
            if issues:
                total += len(issues)
                print(f"\n[{path.relative_to(REPO)}]")
                for it in issues:
                    print(f"  {it}")

        for path in tsx_files:
            issues = scan_tsx(path)
            if issues:
                total += len(issues)
                print(f"\n[{path.relative_to(REPO)}]")
                for it in issues:
                    print(f"  {it}")

    parity = check_token_parity()
    for it in parity:
        print(f"[token-parity] {it}")
    total += len(parity)

    print(f"\n==== design_lint 违规总数: {total} ====")
    if check_only and total:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
