"""FR-001 / FR-002 / FR-005 的单元测试——进化发版链路 bug 修复。

覆盖：
  - AC-001：commit_candidate 承重文件完整性校验给出可定位错误（非裸 rc=128）
  - AC-002：validate_changes 的中间件签名漂移拦截（_middleware_signature_check）
  - AC-005：_MESSAGE_TOOLS 工具名与 writers.py 注册名对齐
"""
from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# 让 evolution/ 进 sys.path，与现有测试一致（test_git_ops_release_isolation 模式）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class CommitCandidateRequiredPathsTest(unittest.TestCase):
    """AC-001 / EDGE-001：承重文件不在 HEAD tree 时给出可定位错误。"""

    def test_missing_required_path_raises_locatable_error_not_raw_rc(self) -> None:
        from app.core import git_ops

        wd = Path(tempfile.mkdtemp())

        def fake_run(cmd, cwd, capture_output, text, timeout):
            # 模拟 cat-file -e HEAD:middleware/artifact_snapshot.py 失败（rc=128）
            return SimpleNamespace(
                returncode=128,
                stderr="fatal: path 'middleware/artifact_snapshot.py' does not exist in 'HEAD'",
            )

        with patch.object(git_ops, "work_dir", return_value=wd), patch.object(
            git_ops, "_changed_paths", return_value=[]
        ), patch.object(
            git_ops, "_all_changed_paths", return_value=set()
        ), patch.object(
            git_ops, "_git_with_author"
        ), patch.object(
            git_ops, "_push_to_bare"
        ), patch.object(
            git_ops, "current_commit", return_value="candidate-commit"
        ), patch.object(
            git_ops.subprocess, "run", side_effect=fake_run
        ), patch.object(
            git_ops, "_is_ignored", return_value=False
        ):
            with self.assertRaises(RuntimeError) as caught:
                git_ops.commit_candidate(
                    "冻结 candidate", required_paths=("middleware/artifact_snapshot.py",),
                )

        msg = str(caught.exception)
        # 必须含可定位信息：哪个文件、根因分类、修复建议——而不是裸 rc=128
        self.assertIn("middleware/artifact_snapshot.py", msg)
        self.assertIn("承重文件完整性校验失败", msg)
        # 给出修复方向（磁盘缺失 / gitignore / 跟踪态损坏三选一）
        self.assertTrue(
            "磁盘" in msg or ".gitignore" in msg or "跟踪态" in msg,
            f"错误信息应给出根因分类，实际: {msg}",
        )

    def test_required_path_present_returns_commit(self) -> None:
        from app.core import git_ops

        wd = Path(tempfile.mkdtemp())

        def fake_run(cmd, cwd, capture_output, text, timeout):
            return SimpleNamespace(returncode=0, stderr="")

        with patch.object(git_ops, "work_dir", return_value=wd), patch.object(
            git_ops, "_changed_paths", return_value=[]
        ), patch.object(
            git_ops, "_all_changed_paths", return_value=set()
        ), patch.object(
            git_ops, "_git_with_author"
        ), patch.object(
            git_ops, "_push_to_bare"
        ), patch.object(
            git_ops, "current_commit", return_value="candidate-commit"
        ), patch.object(
            git_ops.subprocess, "run", side_effect=fake_run
        ):
            result = git_ops.commit_candidate(
                "冻结 candidate", required_paths=("middleware/artifact_snapshot.py",),
            )
        self.assertEqual(result, "candidate-commit")


class MiddlewareSignatureCheckTest(unittest.TestCase):
    """AC-002 / EDGE-002：validate_changes 拦截中间件构造签名漂移。"""

    def _make_pkg(self, init_construction: str, middleware_init_body: str) -> Path:
        """构造一个最小 harness 包：__init__.py 构造中间件 + middleware 定义。

        init_construction: __init__.py 里构造调用的代码片段（如 'BuggyMiddleware(intervention_callback=cb)'）
        middleware_init_body: middleware 模块里 __init__ 的定义体（如 'pass' 表示无参数）
        """
        pkg = Path(tempfile.mkdtemp())
        (pkg / "middleware").mkdir()
        (pkg / "__init__.py").write_text(
            "from .middleware.buggy import BuggyMiddleware\n"
            "def build():\n"
            f"    return [{init_construction}]\n",
            encoding="utf-8",
        )
        (pkg / "middleware" / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "middleware" / "buggy.py").write_text(
            "class BuggyMiddleware:\n"
            f"    def __init__(self, {middleware_init_body}): {middleware_init_body or 'pass'}\n"
            if middleware_init_body
            else "class BuggyMiddleware:\n    pass\n",
            encoding="utf-8",
        )
        return pkg

    def test_signature_drift_is_caught(self) -> None:
        """构造传 intervention_callback=，但类没 __init__ → 漂移被记录。"""
        from app.evolve.agent.tools.flow import _middleware_signature_check

        pkg = Path(tempfile.mkdtemp())
        (pkg / "middleware").mkdir()
        (pkg / "__init__.py").write_text(
            "from .middleware.buggy import BuggyMiddleware\n"
            "def build():\n"
            "    return [BuggyMiddleware(intervention_callback=lambda: None)]\n",
            encoding="utf-8",
        )
        (pkg / "middleware" / "__init__.py").write_text("", encoding="utf-8")
        # 类无 __init__（继承 object，不接受 intervention_callback）——签名漂移
        (pkg / "middleware" / "buggy.py").write_text(
            "class BuggyMiddleware:\n    pass\n", encoding="utf-8",
        )

        errors: list[str] = []
        # 预注册 harness_current 包让检查能 getattr 到类
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "harness_current", pkg / "__init__.py",
            submodule_search_locations=[str(pkg)],
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules["harness_current"] = mod
        try:
            spec.loader.exec_module(mod)
            _middleware_signature_check(pkg, errors)
        finally:
            for k in [k for k in list(sys.modules) if k.startswith("harness_current")]:
                del sys.modules[k]

        self.assertTrue(
            any("签名漂移" in e and "BuggyMiddleware" in e for e in errors),
            f"应检测到签名漂移，实际 errors: {errors}",
        )

    def test_consistent_signature_passes(self) -> None:
        """构造传 intervention_callback=，类 __init__ 接受该参数 → 无错误。"""
        from app.evolve.agent.tools.flow import _middleware_signature_check

        pkg = Path(tempfile.mkdtemp())
        (pkg / "middleware").mkdir()
        (pkg / "__init__.py").write_text(
            "from .middleware.ok import OkMiddleware\n"
            "def build():\n"
            "    return [OkMiddleware(intervention_callback=lambda: None)]\n",
            encoding="utf-8",
        )
        (pkg / "middleware" / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "middleware" / "ok.py").write_text(
            "class OkMiddleware:\n"
            "    def __init__(self, intervention_callback=None):\n"
            "        self.cb = intervention_callback\n",
            encoding="utf-8",
        )

        errors: list[str] = []
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "harness_current", pkg / "__init__.py",
            submodule_search_locations=[str(pkg)],
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules["harness_current"] = mod
        try:
            spec.loader.exec_module(mod)
            _middleware_signature_check(pkg, errors)
        finally:
            for k in [k for k in list(sys.modules) if k.startswith("harness_current")]:
                del sys.modules[k]

        self.assertEqual(errors, [], f"一致签名不应报错，实际 errors: {errors}")


class MessageToolsAlignmentTest(unittest.TestCase):
    """AC-005 / FR-005：_MESSAGE_TOOLS 工具名与 writers.py 注册名对齐。"""

    def test_message_tools_contains_real_writer_names(self) -> None:
        from app.evolve.agent.agent import _MESSAGE_TOOLS

        # writers.py 实际注册的 5 个写工具（EVD-005）
        for real_name in (
            "write_prompt", "write_middleware", "write_tool",
            "write_skill", "write_subagent",
        ):
            self.assertIn(
                real_name, _MESSAGE_TOOLS,
                f"_MESSAGE_TOOLS 缺少真实工具名 {real_name}",
            )

    def test_message_tools_does_not_contain_bogus_names(self) -> None:
        from app.evolve.agent.agent import _MESSAGE_TOOLS

        # EVD-005 笔误的 6 个不存在名字必须全部移除
        for bogus in (
            "write_meta_system", "write_outline_system", "write_writing_system",
            "write_interview_system", "write_detail_outline_system", "write_memory_system",
        ):
            self.assertNotIn(
                bogus, _MESSAGE_TOOLS,
                f"_MESSAGE_TOOLS 仍含不存在工具名 {bogus}（笔误未修）",
            )


class ConcurrencyGuardTest(unittest.TestCase):
    """AC-006 / FR-006 / EDGE-005：并发发版/round 被拒绝（409）。"""

    def test_running_round_rejects_concurrent_publish(self) -> None:
        from fastapi import HTTPException
        from app.evolve.api import _reject_if_round_running, _running_tasks

        # 模拟一个未完成的 round task
        class _FakeTask:
            def done(self) -> bool:
                return False

        _running_tasks["session-concurrent"] = _FakeTask()  # type: ignore[assignment]
        try:
            with self.assertRaises(HTTPException) as caught:
                _reject_if_round_running("session-concurrent", "发版")
            self.assertEqual(caught.exception.status_code, 409)
            self.assertIn("未完成", caught.exception.detail)
        finally:
            _running_tasks.pop("session-concurrent", None)

    def test_completed_round_allows_new_request(self) -> None:
        from app.evolve.api import _reject_if_round_running, _running_tasks

        class _DoneTask:
            def done(self) -> bool:
                return True

        _running_tasks["session-done"] = _DoneTask()  # type: ignore[assignment]
        try:
            # 不抛异常即通过
            _reject_if_round_running("session-done", "发版")
        finally:
            _running_tasks.pop("session-done", None)

    def test_no_running_task_allows_request(self) -> None:
        from app.evolve.api import _reject_if_round_running

        # session 无 task 记录，放行（不抛）
        _reject_if_round_running("session-fresh", "发消息")


class DiscardCleanTest(unittest.TestCase):

    def test_discard_runs_git_clean_after_reset(self) -> None:
        """discard_session 在 git reset --hard 后应追加 git clean -fd。"""
        import os
        import tempfile
        from unittest.mock import MagicMock

        os.environ.setdefault("EVOLUTION_DB", tempfile.mktemp(suffix=".db"))
        from app.core import db as core_db
        core_db.init_db()
        from app.evolve import db as ev_db
        ev_db.create_session("session-discard")
        ev_db.update_session("session-discard", status="pending_review")

        commands_run: list[list[str]] = []

        def fake_run(cmd, cwd, capture_output, text, timeout):
            commands_run.append(cmd)
            # 模拟 git status 输出（clean 后为空）
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        prod = {"version": 5}
        from unittest.mock import AsyncMock
        with patch("app.versioning.registry_repo.get_production_version", return_value=prod), \
             patch("app.versioning.registry_repo.get_version_commit", return_value="target-commit"), \
             patch("app.core.git_ops.work_dir", return_value=Path(tempfile.mkdtemp())), \
             patch("subprocess.run", side_effect=fake_run), \
             patch("app.evolve.api._cleanup_checkpoint", new_callable=AsyncMock):
            import asyncio
            from app.evolve.api import discard_session
            asyncio.run(discard_session("session-discard"))

        # 验证两条命令都执行了：reset --hard 和 clean -fd
        reset_calls = [c for c in commands_run if "reset" in c and "--hard" in c]
        clean_calls = [c for c in commands_run if "clean" in c and "-fd" in c]
        self.assertTrue(reset_calls, f"应执行 git reset --hard，实际命令: {commands_run}")
        self.assertTrue(clean_calls, f"应执行 git clean -fd，实际命令: {commands_run}")


if __name__ == "__main__":
    unittest.main()
