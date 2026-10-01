"""hook 签名硬门测试（REQ-20261001-225509 / FR-001/002/004 / AC-001/002/003/005/007）。

背景：2026-10-01 线上事故——进化产出的 review_gate.py 把 aafter_model 写成
(state, response)，框架按参数名注入，错误参数名收到单参调用 → TypeError。
本组测试锁定三道防线：
  ① hook_protocol 内核：错签名必被抓、对签名/宽松签名零误报（AC-001/002）
  ② inspect_middleware_protocol 工具：输出真实 hook 协议（AC-005）
  ③ validate_changes 集成 + FlowGuard 强制门 + change_log 真实回填
    （AC-003/007 与 validate 集成部分）

隔离策略：无 DB 依赖的纯内核测试 + ctx 直连（set_tool_context）调工具本体，
沿用 test_evolve_evidence_tools.py 的模式。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ.setdefault("EVOLUTION_DB", _tmp_db.name)
os.environ.setdefault("EXECUTOR_URL", "http://127.0.0.1:0")

from langchain.agents.middleware.types import AgentMiddleware

import app.core.db as db
from app.core.settings import settings
from app.evolve.ctx import EvolveContext, set_tool_context
from app.evolve.hook_protocol import (
    check_hook_signatures,
    collect_middleware_classes_from_dir,
    enumerate_base_hooks,
    format_hook_table,
    framework_version,
)
from app.evolve.agent.tools.inspect import make_inspect_tools

REPO_MIDDLEWARE_DIR = Path(__file__).resolve().parent.parent / "harnesses" / "repo" / "middleware"

_old_db = settings.evolution_db


def setUpModule() -> None:
    """临时库（change_log 回填测试走 write_change_log → ev_db.update_session）。"""
    settings.evolution_db = _tmp_db.name
    db._conn = None
    db.init_db()


def tearDownModule() -> None:
    if db._conn is not None:
        db._conn.close()
    db._conn = None
    settings.evolution_db = _old_db
    try:
        os.unlink(_tmp_db.name)
    except OSError:
        pass


# ── ① 内核：协议内省与签名比对（AC-001 / AC-005 基础）──────────────


class BaseHookEnumerationTests(unittest.TestCase):
    def test_base_hooks_enumerated(self):
        """基类全部可覆写 hook 被内省出来，model 系签名是 (state, runtime)。"""
        hooks = enumerate_base_hooks()
        expected = {
            "before_agent", "abefore_agent",
            "before_model", "abefore_model",
            "after_model", "aafter_model",
            "after_agent", "aafter_agent",
            "wrap_model_call", "awrap_model_call",
            "wrap_tool_call", "awrap_tool_call",
        }
        self.assertTrue(expected.issubset(hooks.keys()), f"缺 hook: {expected - set(hooks)}")
        params = [p for p in hooks["aafter_model"].parameters if p != "self"]
        self.assertEqual(params, ["state", "runtime"])

    def test_framework_version_reported(self):
        """版本号可取（evolution 与 executor 框架版本可能漂移，需可见）。"""
        version = framework_version()
        self.assertTrue(version and version[0].isdigit(), f"版本号异常: {version}")


class HookSignatureCheckTests(unittest.TestCase):
    def test_accident_sample_caught(self):
        """AC-001 样本一：事故原样的 aafter_model(state, response) 必被抓。"""

        class ReviewGateMiddleware(AgentMiddleware):
            async def aafter_model(self, state, response):  # 事故原样签名
                return None

        errors = check_hook_signatures([ReviewGateMiddleware])
        self.assertEqual(len(errors), 1, f"应恰好报 1 处: {errors}")
        msg = errors[0]
        for token in ("ReviewGateMiddleware", "aafter_model", "response", "runtime", "state"):
            self.assertIn(token, msg, f"报错缺要素 {token}: {msg}")

    def test_wrap_tool_call_sample_caught(self):
        """AC-001 样本二：wrap_tool_call 缺 handler 参数必被抓（全 hook 覆盖，DEC-004）。"""

        class BadWrapMiddleware(AgentMiddleware):
            def wrap_tool_call(self, request):  # 缺 handler
                return None

        errors = check_hook_signatures([BadWrapMiddleware])
        self.assertEqual(len(errors), 1, f"应恰好报 1 处: {errors}")
        self.assertIn("BadWrapMiddleware", errors[0])
        self.assertIn("handler", errors[0])

    def test_correct_overrides_pass(self):
        """与基类完全一致的覆写（同步+异步、model 系+wrap 系）零报错。"""

        class GoodMiddleware(AgentMiddleware):
            def after_model(self, state, runtime):
                return None

            async def awrap_tool_call(self, request, handler):
                return await handler(request)

            def wrap_model_call(self, request, handler):
                return handler(request)

        self.assertEqual(check_hook_signatures([GoodMiddleware]), [])

    def test_star_kwargs_override_compatible(self):
        """*args/**kwargs 覆写兼容任意调用框架，不算签名违规。"""

        class LooseMiddleware(AgentMiddleware):
            def after_model(self, *args, **kwargs):
                return None

        self.assertEqual(check_hook_signatures([LooseMiddleware]), [])

    def test_unrelated_method_ignored(self):
        """非 hook 的普通方法/构造函数不在校验范围。"""

        class HelperMiddleware(AgentMiddleware):
            def __init__(self, *, limit: int = 3):
                self.limit = limit

            def _private_helper(self, x):
                return x

        self.assertEqual(check_hook_signatures([HelperMiddleware]), [])

    def test_mixin_inherited_bad_hook_caught(self):
        """review 对抗轮实测绕过：mixin 基类带错签名 hook，子类不直接覆写——
        沿 MRO 解析后子类必须被拦。"""

        class BadHookMixin(AgentMiddleware):
            async def aafter_model(self, state, response):  # 错签名在 mixin 里
                return None

        class UsesMixinMiddleware(BadHookMixin):
            pass  # 自己不覆写任何 hook

        errors = check_hook_signatures([UsesMixinMiddleware])
        self.assertTrue(
            any("UsesMixinMiddleware" in e and "aafter_model" in e for e in errors),
            f"mixin 继承的错签名必须拦: {errors}",
        )
        # 同一 mixin 函数被多个子类共享时只报一次
        class AnotherUsesMixinMiddleware(BadHookMixin):
            pass

        errors2 = check_hook_signatures([UsesMixinMiddleware, AnotherUsesMixinMiddleware])
        self.assertEqual(
            sum("aafter_model" in e for e in errors2), 1,
            f"共享 mixin 函数只报一次: {errors2}",
        )

    def test_staticmethod_bad_hook_caught(self):
        """staticmethod 形态的错签名 hook 同样比对（解包后校验）。"""

        class StaticHookMiddleware(AgentMiddleware):
            @staticmethod
            async def aafter_model(state, response):  # 错：第 2 参应为 runtime
                return None

        errors = check_hook_signatures([StaticHookMiddleware])
        self.assertTrue(
            any("StaticHookMiddleware" in e and "aafter_model" in e for e in errors),
            f"staticmethod 错签名必须拦: {errors}",
        )

    def test_args_only_not_loose(self):
        """review 对抗轮发现：*args-only 覆写不豁免——框架按参数名注入，
        *args 收不到注入参数，运行时 TypeError。仅 **kwargs 豁免。"""

        class ArgsOnlyMiddleware(AgentMiddleware):
            def after_model(self, *args):  # 无 **kwargs → 不豁免
                return None

        errors = check_hook_signatures([ArgsOnlyMiddleware])
        self.assertTrue(
            any("ArgsOnlyMiddleware" in e for e in errors),
            f"*args-only 必须报错: {errors}",
        )

        class KwargsOnlyMiddleware(AgentMiddleware):
            def after_model(self, **kwargs):  # **kwargs → 合法宽松
                return None

        self.assertEqual(check_hook_signatures([KwargsOnlyMiddleware]), [])


class RealHarnessRegressionTests(unittest.TestCase):
    def test_production_middleware_zero_false_positive(self):
        """AC-002：现役 harness 包全部中间件过校验，零误报。

        现役文件若存在历史签名问题，此测试暴露（属真实存量问题，非误报）。
        storyline_* 中间件 import contracts.*（仓库根包）——容器内已安装，
        本地测试临时把仓库根加进 sys.path 对齐容器环境（用完即撤：仓库根
        也有 app/ 目录，全局注入会遮蔽 evolution 的 app 包）。
        """
        repo_root = str(Path(__file__).resolve().parent.parent.parent)
        sys.path.insert(0, repo_root)
        self.addCleanup(lambda: sys.path.remove(repo_root))
        classes = collect_middleware_classes_from_dir(REPO_MIDDLEWARE_DIR)
        # 现役规模保护：若加载机制坏掉（收集到 0 个类），测试不得静默通过
        self.assertGreaterEqual(len(classes), 16, f"中间件类收集异常: {[c.__name__ for c in classes]}")
        errors = check_hook_signatures(classes)
        self.assertEqual(errors, [], f"现役中间件存在签名问题: {errors}")


# ── ② inspect_middleware_protocol 工具（AC-005）────────────────────


class InspectProtocolToolTests(unittest.TestCase):
    def _invoke(self):
        for t in make_inspect_tools():
            if t.name == "inspect_middleware_protocol":
                return t.invoke({})
        raise AssertionError("inspect_middleware_protocol 不在探查工具集里")

    def test_tool_lists_hooks_with_version(self):
        out = self._invoke()
        for name in ("aafter_model", "wrap_tool_call", "before_agent"):
            self.assertIn(name, out)
        self.assertIn("runtime", out)
        self.assertIn("handler", out)
        self.assertIn("langchain", out.lower())

    def test_tool_usable_without_ctx(self):
        """只读工具不依赖 session ctx（conversing/finalizing 均可用）。"""
        set_tool_context(None)  # 显式清掉，证明无 ctx 也能答
        out = self._invoke()
        self.assertIn("aafter_model", out)

    def test_framework_unavailable_reports_clearly(self):
        """FR-002 失败语义：框架内省失败 → 明确报错，不静默、不猜签名。"""
        from unittest import mock
        from app.evolve.agent.tools import inspect as inspect_mod
        with mock.patch.object(
            inspect_mod, "format_hook_table", side_effect=RuntimeError("boom"),
        ):
            out = self._invoke()
        self.assertIn("无法内省", out)
        self.assertIn("不要凭记忆", out)
        self.assertNotIn("aafter_model", out)


# ── ③ validate_changes 集成 + FlowGuard 门 + change_log 回填────────
# （AC-001 的 validate_changes 全链路、AC-003、AC-007）


def _make_pkg_with_bad_middleware(root: Path) -> Path:
    """临时 harness 包：__init__ 空壳 + 一个错签名中间件（事故原样）。"""
    pkg = root / "pkg"
    (pkg / "middleware").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "middleware" / "review_gate.py").write_text(
        "from langchain.agents.middleware.types import AgentMiddleware\n"
        "\n"
        "\n"
        "class ReviewGateMiddleware(AgentMiddleware):\n"
        "    async def aafter_model(self, state, response):\n"
        "        return None\n",
        encoding="utf-8",
    )
    return pkg


class ValidateChangesIntegrationTests(unittest.TestCase):
    def setUp(self):
        import types
        from unittest import mock

        from app.evolve.agent.tools import flow as flow_mod
        from app.evolve.agent.tools.flow import make_flow_tools

        tmp = tempfile.mkdtemp(prefix="hook_gate_pkg_")
        self._pkg = _make_pkg_with_bad_middleware(Path(tmp))
        # 直接 patch flow 模块持有的 settings 引用：既有 benchmark 测试会
        # importlib.reload(settings) 重建单例，flow.py 与本测试可能拿到
        # 不同实例（test_events_stream.py:269 已记录该现象）——改走模块
        # 属性 patch，与单例身份解耦。
        self._patcher = mock.patch.object(
            flow_mod, "settings",
            types.SimpleNamespace(harness_work_dir_path=self._pkg),
        )
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        self.ctx = EvolveContext("sess-hook-gate")
        set_tool_context(self.ctx)
        self.addCleanup(lambda: set_tool_context(None))
        # validate_changes 会把 harness_current* 注册进 sys.modules，
        # 不清理会污染后续测试（如 test_release_pipeline 的装配夹具）
        self.addCleanup(self._purge_harness_current)
        self.flow_tools = {t.name: t for t in make_flow_tools()}

    @staticmethod
    def _purge_harness_current() -> None:
        for k in [
            k for k in list(sys.modules)
            if k == "harness_current" or k.startswith("harness_current.")
        ]:
            del sys.modules[k]

    def test_validate_catches_bad_hook_signature(self):
        """AC-001 全链路：validate_changes 对错签名包报失败，四要素齐全。"""
        out = self._run("validate_changes")
        self.assertIn("校验失败", out)
        for token in ("ReviewGateMiddleware", "aafter_model", "response", "runtime"):
            self.assertIn(token, out, f"报错缺要素 {token}: {out}")

    def test_validate_records_result_to_ctx(self):
        """校验结果写入 ctx（change_log 回填与 FlowGuard 门的依据）。"""
        self._run("validate_changes")
        self.assertIsNotNone(self.ctx.validation_result)
        self.assertFalse(self.ctx.validation_result["passed"])
        self.assertTrue(self.ctx.validation_result["errors"])

    def _run(self, name, **kwargs):
        return self.flow_tools[name].invoke(kwargs)


class FlowGuardGateTests(unittest.TestCase):
    """AC-003：未跑 validate → 拦 change_log；跑了（即使失败）→ 放行。"""

    def _guard(self) -> str | None:
        from app.evolve.agent.middleware.flow_guard import FlowGuardMiddleware
        return FlowGuardMiddleware._check_change_log_guard()

    def setUp(self):
        self.ctx = EvolveContext("sess-guard")
        set_tool_context(self.ctx)

    def tearDown(self):
        set_tool_context(None)

    def test_no_design_doc_blocked_first(self):
        """原有产出依赖优先：没有 design_doc 时先报缺 design_doc。"""
        self.assertIn("design_doc", self._guard())

    def test_unvalidated_code_blocked(self):
        """AC-003：有 design_doc、没跑 validate → 拦，提示先跑校验。"""
        self.ctx.design_doc_path = "/tmp/design_doc.md"
        msg = self._guard()
        self.assertIsNotNone(msg)
        self.assertIn("validate", msg)

    def test_failed_validation_still_allowed(self):
        """DEC-005：validate 跑过但失败 → 放行收尾（失败清单进 change_log）。"""
        self.ctx.design_doc_path = "/tmp/design_doc.md"
        self.ctx.validation_result = {"passed": False, "errors": ["x"]}
        self.assertIsNone(self._guard())

    def test_new_code_after_validation_blocked(self):
        """validate 之后又写新代码未重跑 → 拦（校验必须覆盖最终代码）。"""
        self.ctx.design_doc_path = "/tmp/design_doc.md"
        self.ctx.validation_result = {"passed": True, "errors": []}
        self.ctx.code_mutations_since_validate = 2
        msg = self._guard()
        self.assertIsNotNone(msg)
        self.assertIn("validate", msg)

    def test_clean_pass_allowed(self):
        """design_doc + validate 通过 + 无新增代码 → 放行。"""
        self.ctx.design_doc_path = "/tmp/design_doc.md"
        self.ctx.validation_result = {"passed": True, "errors": []}
        self.ctx.code_mutations_since_validate = 0
        self.assertIsNone(self._guard())


class CodeMutationCounterTests(unittest.TestCase):
    """DEC-003 计数器生命周期（review 修复轮）：写 .py 递增、validate 归零、
    非 .py 不动、write_skill 携 .py 脚本同样递增。"""

    def setUp(self):
        from types import SimpleNamespace

        from app.evolve.agent.tools.flow import make_flow_tools
        from app.evolve.agent.tools.writers import make_writer_tools

        class _StubBackend:
            """最小 backend 桩：write/edit 全成功（计数逻辑不依赖真实落盘）。"""

            def __init__(self):
                self.written: list[str] = []

            def write(self, path, content):
                self.written.append(path)
                return SimpleNamespace(error=None)

        self.backend = _StubBackend()
        self.ctx = EvolveContext("sess-counter")
        set_tool_context(self.ctx)
        self.addCleanup(lambda: set_tool_context(None))
        self.writer_tools = {t.name: t for t in make_writer_tools(self.backend)}
        self.flow_tools = {t.name: t for t in make_flow_tools()}

    def test_py_writes_increment_counter(self):
        """write_middleware（.py）递增；write_prompt（.md）不动。"""
        self.assertEqual(self.ctx.code_mutations_since_validate, 0)
        self.writer_tools["write_middleware"].invoke({"name": "pacing", "code": "x = 1"})
        self.assertEqual(self.ctx.code_mutations_since_validate, 1)
        self.writer_tools["write_prompt"].invoke({"name": "style", "content": "# t"})
        self.assertEqual(self.ctx.code_mutations_since_validate, 1)

    def test_write_skill_py_script_increments(self):
        """review 对抗轮发现：write_skill 写 .py 脚本也必须计数，
        否则 validate 后写脚本可携「校验通过」结论绕门。"""
        self.writer_tools["write_skill"].invoke({"path": "s/helper.py", "content": "x = 1"})
        self.assertEqual(self.ctx.code_mutations_since_validate, 1)
        self.writer_tools["write_skill"].invoke({"path": "s/guide.md", "content": "# t"})
        self.assertEqual(self.ctx.code_mutations_since_validate, 1)  # .md 不计

    def test_validate_resets_counter_and_guards_change_log(self):
        """validate 归零 + FlowGuard 门随计数状态开合（完整生命周期）。"""
        from app.evolve.agent.middleware.flow_guard import FlowGuardMiddleware
        from app.core import settings as settings_mod

        self.ctx.design_doc_path = "/tmp/design_doc.md"
        self.writer_tools["write_middleware"].invoke({"name": "pacing", "code": "x = 1"})
        self.assertEqual(self.ctx.code_mutations_since_validate, 1)
        self.ctx.validation_result = {"passed": True, "errors": []}
        # validate 之后又有新代码 → FlowGuard 拦
        self.assertIsNotNone(FlowGuardMiddleware._check_change_log_guard())
        # 重新 validate（干净临时包）→ 计数归零 → 门开
        import tempfile as _tf
        import types as _types
        from unittest import mock as _mock
        from app.evolve.agent.tools import flow as flow_mod
        clean = Path(_tf.mkdtemp(prefix="hook_gate_clean_"))
        (clean / "__init__.py").write_text("", encoding="utf-8")
        with _mock.patch.object(
            flow_mod, "settings",
            _types.SimpleNamespace(harness_work_dir_path=clean),
        ):
            out = self.flow_tools["validate_changes"].invoke({})
        self.assertIn("校验通过", out)
        self.assertEqual(self.ctx.code_mutations_since_validate, 0)
        self.assertIsNone(FlowGuardMiddleware._check_change_log_guard())
        self.assertTrue(self.ctx.validation_result["passed"])


class ChangeLogBackfillTests(unittest.TestCase):
    """AC-007：change_log 的校验结果回填真实三态，不再硬编码「通过」。"""

    def setUp(self):
        from unittest import mock

        from app.evolve.agent.tools.flow import make_flow_tools
        from app.evolve import docs as docs_mod

        self.ctx = EvolveContext("sess-changelog-backfill")
        set_tool_context(self.ctx)
        self.addCleanup(lambda: set_tool_context(None))
        self.ctx.design_doc_path = "/tmp/design_doc.md"
        self.flow_tools = {t.name: t for t in make_flow_tools()}
        # session_dir 是 __file__ 根路径（真实 data 目录）——patch 到临时目录，
        # 测试不往 data/evolve_workspace 落文件
        self._tmp = tempfile.mkdtemp(prefix="hook_gate_docs_")
        self._patcher = mock.patch.object(
            docs_mod, "session_dir",
            lambda session_id: Path(self._tmp),
        )
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _write(self) -> str:
        return self.flow_tools["write_change_log"].invoke({
            "applied": [], "summary": "test",
        })

    def _read_body(self) -> str:
        return (Path(self._tmp) / "change_log.md").read_text(encoding="utf-8")

    def test_failed_validation_backfilled(self):
        """validate 失败 → change_log 记「失败」+ 真实失败清单。"""
        self.ctx.validation_result = {
            "passed": False,
            "errors": ["ReviewGateMiddleware.aafter_model: hook 参数应为 ['state', 'runtime']"],
        }
        self.ctx.code_mutations_since_validate = 0
        out = self._write()
        self.assertIn("已产出", out)
        body = self._read_body()
        self.assertIn("校验结果：失败", body)
        self.assertIn("ReviewGateMiddleware", body)

    def test_passed_validation_backfilled(self):
        self.ctx.validation_result = {"passed": True, "errors": []}
        self.ctx.code_mutations_since_validate = 0
        self._write()
        body = self._read_body()
        self.assertIn("校验结果：通过", body)

    def test_never_validated_recorded_as_not_run(self):
        """未跑 validate（FlowGuard 应拦，此测双保险路径）→ 记「未校验」。"""
        self.ctx.validation_result = None
        self._write()
        body = self._read_body()
        self.assertIn("校验结果：未校验", body)
        self.assertNotIn("校验结果：通过", body)


if __name__ == "__main__":
    unittest.main()
