"""程序体检计算（REQ-20261011-142529 FR-007，DEC-007/016/13）。

纯函数：读 outline/ 产物（赛道/台账/章纲批/力量体系/卷纲），按赛道参数
计算节奏体检报告，返回 markdown 文本。只建议、不拦截（DEC-007 观测模式）。

消费方：tools/confirm_with_user（storystructure 泳道拍板载荷自动附本报告，
DEC-016——review 子代理延后，体检直接呈现给用户与模型）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from contracts.outline_ledger_contract import parse_chapter_rows, parse_ledger_rows

# 赛道参数配置（相对 harness 包根 middleware/；默认番茄系，FR-009）
_CONFIG_RELPATH = Path("middleware") / "checkup_config.json"


def load_checkup_config(pkg_dir: Path) -> dict:
    try:
        return json.loads((Path(pkg_dir) / _CONFIG_RELPATH).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 配置缺失用内置默认
        return {}


def _track_params(config: dict, workspace: Path) -> dict:
    params = dict(config.get("default", {}))
    track = ""
    race = workspace / "outline" / "00-赛道.md"
    if race.is_file():
        m = re.search(r"平台[：:]\s*(\S+)", race.read_text(encoding="utf-8"), re.M)
        if m:
            track = m.group(1).strip()
    params.update(config.get("tracks", {}).get(track, {}))
    params.setdefault("max_big_payoff_gap", 5)
    params.setdefault("stale_chapters", 30)
    return params


def _read(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="gb18030", errors="replace")


def _num(s: str) -> int | None:
    s = (s or "").strip()
    return int(s) if s.isdigit() else None


def _batch_files(workspace: Path) -> list[Path]:
    root = workspace / "outline" / "06-章纲"
    if not root.is_dir():
        return []
    return sorted(root.rglob("*.md"), key=lambda p: p.as_posix())


def _realm_slots(workspace: Path) -> int | None:
    """03-力量体系「升级-分卷对应表」的数据行数（境界段数）。"""
    text = _read(workspace / "outline" / "03-力量体系.md")
    idx = text.find("升级-分卷对应表")
    if idx < 0:
        return None
    tail = text[idx:]
    rows = 0
    for ln in tail.splitlines():
        s = ln.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if set("".join(cells)) <= {"-", " ", ":"}:
                continue  # 分隔行
            if cells and re.fullmatch(r"\d+", cells[0]):
                rows += 1
    return rows or None


def compute_checkup(workspace: Path, config: dict | None = None) -> str:
    """计算体检报告（markdown）。无章纲且无台账时返回空串（拍板载荷不附）。"""
    workspace = Path(workspace)
    params = _track_params(config or {}, workspace)
    lines: list[str] = []

    chapters: list[dict[str, str]] = []
    for f in _batch_files(workspace):
        chapters.extend(parse_chapter_rows(_read(f)))

    ledger_rows = parse_ledger_rows(_read(workspace / "outline" / "期待台账.md"))
    if not chapters and not ledger_rows:
        return ""

    lines.append("—— 程序体检报告（只建议不拦截；红项须在拍板前逐条回应：修掉或说明为何刻意）——")
    last_ch = max((_num(c["章"]) or 0) for c in chapters) if chapters else 0
    lines.append(f"[章纲覆盖] {len(_batch_files(workspace))} 批 / {len(chapters)} 章（最新第 {last_ch} 章）")

    if chapters:
        missing_payoff = [c["章"] for c in chapters if not c["爽点"].strip()]
        missing_hook = [c["章"] for c in chapters if not c["章末钩子"].strip()]
        lines.append(
            "[小爽点] " + ("全部章齐备 ✓" if not missing_payoff else f"🔴 缺失：第 {','.join(missing_payoff)} 章")
        )
        lines.append(
            "[章末钩子] " + ("全部章齐备 ✓" if not missing_hook else f"🔴 缺失：第 {','.join(missing_hook)} 章")
        )
        bigs = [(_num(c["章"]) or 0) for c in chapters if "大" in c["爽点"]]
        gaps: list[tuple[int, int]] = []
        prev = 0
        for b in bigs:
            if b - prev > params["max_big_payoff_gap"]:
                gaps.append((prev, b))
            prev = b
        if last_ch - prev > params["max_big_payoff_gap"]:
            gaps.append((prev, last_ch))
        lines.append(
            f"[大爽点间隔] 阈值 ≤ {params['max_big_payoff_gap']} 章："
            + ("节奏达标 ✓" if not gaps else "🔴 超限：" + "；".join(f"第 {a}→{b} 章（{b - a} 章）" for a, b in gaps))
        )

    if ledger_rows:
        active = [r for r in ledger_rows if r["状态"] == "活跃"]
        done = [r for r in ledger_rows if r["状态"] == "已兑现"]
        dropped = [r for r in ledger_rows if r["状态"] == "已放弃"]
        red: list[str] = [r["ID"] for r in ledger_rows if r["状态"] == "悬置🔴"]
        for r in active:
            anchor = _num(r["最近推进章"]) or _num(r["埋设章"])
            if anchor is not None and last_ch - anchor > params["stale_chapters"]:
                red.append(f"{r['ID']}（{last_ch - anchor} 章未推进）")
        lines.append(
            f"[期待台账] 活跃 {len(active)} / 已兑现 {len(done)} / 已放弃 {len(dropped)}"
            + ("；🔴 悬置待处理：" + "、".join(dict.fromkeys(red)) if red else "；无悬置 ✓")
        )

    slots = _realm_slots(workspace)
    if slots is not None:
        vols = len(list((workspace / "outline" / "05-卷纲").glob("*.md")))
        remain = slots - (vols + 1)
        lines.append(
            f"[境界余量] 境界段 {slots} 段 / 已排卷 {vols} 卷 → 下一卷余量 "
            + ("充足 ✓" if remain >= 1 else f"🔴 不足（剩 {remain}）——卷末盘点防崩三问，必要时提交回炉提案")
        )
    return "\n".join(lines)


def render_checkup_section(workspace: Path, pkg_dir: Path) -> str:
    """拍板载荷附加段（compute 为空时返回空串）。"""
    report = compute_checkup(workspace, load_checkup_config(pkg_dir))
    return f"\n\n{report}" if report else ""


__all__ = ["compute_checkup", "load_checkup_config", "render_checkup_section"]
