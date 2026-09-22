"""QuotaConvergenceMiddleware 三态导航与护栏累计语义测试（REQ-20260922-162823 FR-001/FR-002，review P1 修复）。

覆盖（DEC-011 同一判定器的行为契约）：
  - target=None（minimal 档）软终止：恒不注入
  - 未达标 → 注入「继续下一轮增量」+ 差距
  - 已达标 → 注入「已达标」收尾指令
  - 预算耗尽 → 「预算上限」强制收尾（优先于未达标分支）
  - before_agent 重置轮次计数
  - 单线护栏 reset_per_invocation=False（v14）跨委托累计 / =True（v13）按委托重置

模块经 spec 加载（harness 包不在主进程 sys.path；依赖 contracts 由测试运行
环境提供，同套件其他 harness 测试的模式）。
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXECUTOR_DIR = _REPO_ROOT / "executor"
_WORKING_PKG = _REPO_ROOT / "evolution" / "harnesses" / "repo"

# 本地运行修复：stdlib platform 被 contracts/platform 遮蔽（同 cost_stats 模式）
if not hasattr(sys.modules.get("platform"), "python_implementation"):
    _stdlib_platform = Path(sys.base_prefix) / "Lib" / "platform.py"
    if _stdlib_platform.is_file():
        _spec_p = importlib.util.spec_from_file_location("platform", str(_stdlib_platform))
        _mod_p = importlib.util.module_from_spec(_spec_p)
        sys.modules["platform"] = _mod_p
        _spec_p.loader.exec_module(_mod_p)

sys.path.insert(0, str(_EXECUTOR_DIR))  # langchain 链 import executor venv 已装；app 不被此测试触碰


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


from contracts.storybuilding_quota import QuotaTarget  # noqa: E402

_qc = _load("smoke_quota_convergence", _WORKING_PKG / "middleware" / "quota_convergence.py")

# storyline_single_line_limit.py 含相对导入（from .path_guard import ...）——
# 构造命名空间父包提供包上下文，避免拉起整个 harness __init__ 链（app 撞名）
import types

_ns_pkg = types.ModuleType("harness_mw_ns")
_ns_pkg.__path__ = [str(_WORKING_PKG / "middleware")]
sys.modules["harness_mw_ns"] = _ns_pkg
_sl = _load(
    "harness_mw_ns.storyline_single_line_limit",
    _WORKING_PKG / "middleware" / "storyline_single_line_limit.py",
)

_INDEX_ACHIEVED = """## 故事线一览表

| ID | 名称 | 类型 | 状态 |
|----|------|------|------|
| S01 | a | 主线 | 活跃 |
| S02 | b | 主线 | 活跃 |
| S03 | c | 主线 | 活跃 |
| S04 | d | 主线 | 活跃 |
| S05 | e | 主线 | 活跃 |
| S06 | f | 支线 | 活跃 |
| S07 | g | 角色线 | 活跃 |
"""


class QuotaConvergenceNavigationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        self.target = QuotaTarget(5, 1, 1, 0, source="core")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _mw(self, target=None, max_model_calls=120):
        return _qc.QuotaConvergenceMiddleware(
            self.ws, target, max_model_calls=max_model_calls,
        )

    def test_soft_termination_when_no_target(self) -> None:
        """DEC-013：minimal 档（无配比）恒不注入导航（软终止）。"""
        mw = self._mw(target=None)
        self.assertIsNone(mw._build_message())
        self.assertIsNone(mw._build_message())

    def test_not_achieved_drives_continue(self) -> None:
        """空工作区（初构前）→ 未达标 → 继续增量 + 差距明细。"""
        mw = self._mw(target=self.target)
        msg = mw._build_message()
        self.assertIsNotNone(msg)
        self.assertIn("未达标", msg.content)
        self.assertIn("继续下一轮增量", msg.content)
        self.assertIn("主线还差5条", msg.content)

    def test_achieved_drives_wrap_up(self) -> None:
        """一览表达标 → 已达标收尾指令（含 review 收尾要求）。"""
        (self.ws / "storyline.md").write_text(_INDEX_ACHIEVED, encoding="utf-8")
        mw = self._mw(target=self.target)
        msg = mw._build_message()
        self.assertIn("已达标", msg.content)
        self.assertIn("review", msg.content)

    def test_budget_exhaustion_overrides_not_achieved(self) -> None:
        """预算耗尽优先于未达标：第 max+1 轮注入强制收尾。"""
        mw = self._mw(target=self.target, max_model_calls=2)
        first = mw._build_message()   # 第 1 轮：继续
        second = mw._build_message()  # 第 2 轮：继续
        third = mw._build_message()   # 第 3 轮：预算耗尽
        self.assertIn("继续下一轮增量", first.content)
        self.assertIn("继续下一轮增量", second.content)
        self.assertIn("预算上限", third.content)
        self.assertNotIn("继续下一轮增量", third.content)

    def test_default_budget_below_recursion_limit(self) -> None:
        """默认预算在执行端 recursion_limit=300 超步内可软着陆（review P1，实测口径）。

        实测（deepagents 0.6.1 栈）：每模型调用 5 超步（2×before_model + model
        + after_model + tools），300 超步硬死于第 60 次调用；预算×5 + 60 收尾
        余量必须 ≤ 300。
        """
        self.assertLessEqual(_qc.DEFAULT_MAX_MODEL_CALLS * 5 + 60, 300)

    def test_before_agent_resets_cycle(self) -> None:
        """每次运行（graph 执行）开始时轮次计数清零。"""
        mw = self._mw(target=self.target, max_model_calls=1)
        mw._build_message()
        exhausted = mw._build_message()
        self.assertIn("预算上限", exhausted.content)
        mw.before_agent(state={}, runtime={})
        fresh = mw._build_message()
        self.assertIn("继续下一轮增量", fresh.content)


class StorylineLimitAccumulationTest(unittest.TestCase):
    """单线护栏两种计数周期语义（v13 按委托重置 / v14 跨委托累计）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        (self.ws / "storyline").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _write_request(index: int):
        return SimpleNamespace(tool_call={
            "name": "write_file",
            "args": {"file_path": f"/storyline/S{index:02d}-x.md"},
            "id": f"t{index}",
        })

    def test_reset_per_invocation_resets_between_delegations(self) -> None:
        """v13 语义：before_agent 清零，两次委托各允许 max 条新线。"""
        mw = _sl.StorylineSingleLineLimitMiddleware(self.ws, max_new_lines=1)
        self.assertIsNone(mw._maybe_block(self._write_request(1)))  # 委托1：放行
        self.assertIsNotNone(mw._maybe_block(self._write_request(2)))  # 委托1：拦截
        (self.ws / "storyline" / "S01-x.md").write_text("x", encoding="utf-8")
        mw.before_agent(state={}, runtime={})  # 委托2 开始：重置
        self.assertIsNone(mw._maybe_block(self._write_request(2)))

    def test_accumulate_across_delegations_when_disabled(self) -> None:
        """v14 语义（reset_per_invocation=False）：跨委托累计，运行级绝对上限。"""
        mw = _sl.StorylineSingleLineLimitMiddleware(
            self.ws, max_new_lines=2, reset_per_invocation=False,
        )
        self.assertIsNone(mw._maybe_block(self._write_request(1)))
        (self.ws / "storyline" / "S01-x.md").write_text("x", encoding="utf-8")
        self.assertIsNone(mw._maybe_block(self._write_request(2)))
        (self.ws / "storyline" / "S02-x.md").write_text("x", encoding="utf-8")
        mw.before_agent(state={}, runtime={})  # 新委托：不重置
        blocked = mw._maybe_block(self._write_request(3))  # 运行内第 3 条新线：拦截
        self.assertIsNotNone(blocked)


if __name__ == "__main__":
    unittest.main()
