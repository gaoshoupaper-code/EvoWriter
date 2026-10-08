"""写工具并发串行化回归测试（线上 2026-10-08 session cd9382800535 发布被拒根因）。

langgraph ToolNode 用 asyncio.gather 并发执行同一轮的多个 tool_call（同步
工具经 run_in_executor 落线程池）。修复前：模型一轮并发 4 个 edit_source 打
同一文件，各线程「读全文 → 替换 → O_TRUNC 整文件重写」交叉执行，后写的把
先写的字节流拦腰覆盖，文件变成非法 UTF-8（全角括号被截掉首字节），发布侧
probe 干净 checkout 装配即 UnicodeDecodeError。

修复（writers.py _HARNESS_WRITE_LOCK 写串行化 + validate_changes UTF-8
全包扫查）的行为断言：
  1. 并发同文件 edit 不再产生非法 UTF-8，且全部编辑都落盘；
  2. 并发不同文件写互不干扰（串行化不改变成功语义）；
  3. validate_changes 对坏字节 .md 报「编码损坏」（提前到落地阶段拦截）。
"""
from __future__ import annotations

import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_PKG = REPO_ROOT / "evolution" / "harnesses" / "repo"

EVO_ROOT = REPO_ROOT / "evolution"
if str(EVO_ROOT) not in sys.path:
    sys.path.insert(0, str(EVO_ROOT))


@pytest.fixture()
def pkg_copy(tmp_path: Path) -> Path:
    """真实 harness 包的干净副本（含 .git 排除，不污染工作树）。"""
    dst = tmp_path / "pkg"
    shutil.copytree(
        HARNESS_PKG, dst,
        ignore=shutil.ignore_patterns("__pycache__", ".git"),
    )
    return dst


class _FakeCtx:
    """最小工具上下文：计数器 + step 事件收集。"""

    def __init__(self):
        self.code_mutations_since_validate = 0
        self.events: list[tuple] = []

    def emit_step(self, tool, status, **kw):
        self.events.append((tool, status, kw))


def _make_writers(pkg: Path):
    from deepagents.backends.filesystem import FilesystemBackend

    from app.evolve.agent.tools.writers import make_writer_tools

    tools = {
        t.name: t for t in make_writer_tools(
            FilesystemBackend(root_dir=str(pkg), virtual_mode=True)
        )
    }
    return tools, _FakeCtx()


def _invoke(tool, ctx, **kw):
    """langchain @tool 调用：上下文经 ctxvar 注入（每线程各自注入）。"""
    from app.evolve.ctx import set_tool_context

    set_tool_context(ctx)  # type: ignore[arg-type]  # 测试桩鸭子类型
    return tool.invoke(kw)


def _run_concurrent(tool, ctx, calls: list[dict], workers: int = 4) -> list[str]:
    """并发调用同一工具（模拟 ToolNode 同轮 gather；barrier 对齐起跑线）。"""
    barrier = threading.Barrier(len(calls))

    def run(call: dict) -> str:
        barrier.wait()
        return _invoke(tool, ctx, **call)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run, calls))


def test_concurrent_same_file_edits_stay_valid_utf8(pkg_copy):
    """同轮 4 个 edit_source 并发打同一文件：字节完整 + 4 处编辑全部落盘。

    复刻线上事故形态——同一提示词文件 4 处互不重叠的替换并发执行。
    """
    tools, ctx = _make_writers(pkg_copy)
    target = "prompts/storybuilding_review.md"
    before = (pkg_copy / "prompts" / "storybuilding_review.md").read_text(encoding="utf-8")

    edits = [
        {"file_path": target, "old_string": before[:20], "new_string": before[:20] + "甲"},
        {"file_path": target, "old_string": before[21:41], "new_string": "乙" + before[21:41]},
        {"file_path": target, "old_string": before[42:62], "new_string": before[42:62] + "丙"},
        {"file_path": target, "old_string": before[63:83], "new_string": "丁" + before[63:83]},
    ]
    assert len({e["old_string"] for e in edits}) == 4  # 4 处互不重叠

    results = _run_concurrent(tools["edit_source"], ctx, edits)
    assert all("已编辑" in r for r in results), results

    # 修复点 1：文件仍是合法 UTF-8（修复前交叉写会产生孤立续字节）
    after = (pkg_copy / "prompts" / "storybuilding_review.md").read_text(encoding="utf-8")
    # 修复点 2：4 处编辑全部落盘（修复前部分线程的整文件重写会覆盖兄弟线程；
    # 测试用的 new_string 内嵌 old_string，只断言新文案在位）
    for e in edits:
        assert e["new_string"] in after
    assert after.count("甲") == 1 and after.count("丙") == 1


def test_concurrent_different_file_writes_all_land(pkg_copy):
    """并发写不同文件：串行化不改变成功语义，两个新文件都创建。"""
    tools, ctx = _make_writers(pkg_copy)
    calls = [
        {"name": "ser_a", "content": "# A\n"},
        {"name": "ser_b", "content": "# B\n"},
    ]
    results = _run_concurrent(tools["write_prompt"], ctx, calls)
    assert all("已创建" in r for r in results), results
    assert (pkg_copy / "prompts" / "ser_a.md").read_text(encoding="utf-8") == "# A\n"
    assert (pkg_copy / "prompts" / "ser_b.md").read_text(encoding="utf-8") == "# B\n"


def test_validate_changes_catches_corrupt_utf8(pkg_copy, monkeypatch):
    """validate_changes 检出坏字节 .md：编码损坏在落地阶段报错，而非发布 probe。"""
    from app.core.settings import settings
    from app.evolve.agent.tools.flow import make_flow_tools

    bad = pkg_copy / "prompts" / "corrupt_probe.md"
    bad.write_bytes("合法前缀。".encode("utf-8") + b"\xbc\x89\n")

    monkeypatch.setattr(settings, "harness_work_dir", str(pkg_copy))
    validate = next(t for t in make_flow_tools() if t.name == "validate_changes")
    result = _invoke(validate, _FakeCtx())

    assert "编码损坏" in result, result
    assert "corrupt_probe.md" in result, result
