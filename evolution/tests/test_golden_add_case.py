"""golden case 受控新增测试（REQ-20260920-104714 / FR-004 / AC-007）。

覆盖：
- happy path：文件 + dataset_meta 登记 + git 提交 + revision 重锁四件套齐全
- 空内容拒绝：400 且零残留
- git 提交失败：整体回滚（文件删、登记删、revision 回旧值）
- case_id 自动编号：接现有最大编号
"""
import atexit
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmpdir = Path(tempfile.mkdtemp())
os.environ["EXECUTOR_URL"] = "http://127.0.0.1:0"


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root),
         "-c", "user.name=test", "-c", "user.email=test@test",
         *args],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {args} 失败: {proc.stderr}")
    return proc.stdout.strip()


# temp git 仓库 + evalset 基线（case-001 已存在；DB 基线在各类 setUp 里建）
_repo_root = _tmpdir / "repo"
_evalset_root = _repo_root / "evolution" / "data" / "evalset"
_baseline = _evalset_root / "golden" / "case-001"
_baseline.mkdir(parents=True)
(_baseline / "demand.md").write_text("# 基线需求\n题材：玄幻", encoding="utf-8")
_git(_repo_root, "init", "-b", "main")
_git(_repo_root, "add", ".")
_git(_repo_root, "commit", "-m", "baseline")


def _cleanup_tmpdir() -> None:
    import shutil

    import app.core.db as db

    db._conn = None
    shutil.rmtree(_tmpdir, ignore_errors=True)


atexit.register(_cleanup_tmpdir)


def _evalset_to_tmp():
    """把 evalset_root 指到 temp 仓库（revision/layer_root 同模块解析，一并生效）。"""
    return patch("app.common.evalset.evalset_root", return_value=_evalset_root)


class GoldenAddCaseTest(unittest.TestCase):
    """每类独立 DB（注入连接，免疫 EVOLUTION_DB 被「后 import 模块」覆盖，
    与 test_benchmark_concurrency 同模式）。"""

    def setUp(self):
        self._dbfile = _tmpdir / f"test-{id(self)}.db"
        self._prev_db_env = os.environ.get("EVOLUTION_DB")
        os.environ["EVOLUTION_DB"] = str(self._dbfile)
        import importlib
        import sqlite3

        import app.core.settings as settings_mod

        importlib.reload(settings_mod)
        import app.core.db as db_mod

        conn = sqlite3.connect(self._dbfile, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        db_mod._conn = conn
        db_mod._master_key_cache = None
        db_mod.init_db()

        from app.dataset import repo as dataset_repo
        from app.dataset import revision

        dataset_repo.register_case(case_id="case-001", layer="golden", created_by="test")
        with _evalset_to_tmp():
            dataset_repo.update_demand_revision("case-001", revision.compute_golden_revision())

    def tearDown(self):
        import app.core.db as db

        try:
            db.get_conn().close()
        except Exception:
            pass
        # 还原环境变量（同 test_benchmark_judge_select 的理由）
        if self._prev_db_env is None:
            os.environ.pop("EVOLUTION_DB", None)
        else:
            os.environ["EVOLUTION_DB"] = self._prev_db_env

    def _create(self, demand_md: str):
        from app.dataset import api as dataset_api

        with _evalset_to_tmp(), \
             patch.object(dataset_api, "_try_git_push", return_value=None):
            return dataset_api.create_golden_case(
                dataset_api.GoldenCaseCreateRequest(demand_md=demand_md)
            )

    def test_happy_path_all_four_artifacts(self):
        """AC-007：文件 + 登记 + git 提交 + revision 重锁四件套齐全。"""
        from app.dataset import repo as dataset_repo
        from app.dataset import api as dataset_api

        with _evalset_to_tmp():
            old_rev = dataset_repo.get_golden_revision()

        resp = self._create("# 新需求\n题材：都市异能，主角是外卖员")

        new_case_dir = _evalset_root / "golden" / resp["case_id"]
        # 1. 文件
        self.assertTrue((new_case_dir / "demand.md").exists())
        # 2. dataset_meta 登记
        meta = dataset_repo.get(resp["case_id"])
        self.assertIsNotNone(meta)
        self.assertEqual(meta["layer"], "golden")
        # 3. git 提交（HEAD message 含 case_id）
        head_msg = _git(_repo_root, "log", "-1", "--pretty=%s")
        self.assertIn(resp["case_id"], head_msg)
        self.assertEqual(resp["git_commit"], _git(_repo_root, "rev-parse", "HEAD"))
        # 4. revision 重锁且变化
        self.assertNotEqual(resp["golden_revision"], old_rev)
        with _evalset_to_tmp():
            self.assertEqual(dataset_repo.get_golden_revision(), resp["golden_revision"])
        self.assertIsNone(resp["git_push_warning"])

    def test_reject_empty_content_zero_residue(self):
        """空/空白内容 → 400，golden 目录与新增前完全一致（零残留）。"""
        from fastapi import HTTPException

        before = sorted(d.name for d in (_evalset_root / "golden").iterdir())

        for empty in ("", "   \n\n  "):
            with self.assertRaises(HTTPException) as ctx:
                self._create(empty)
            self.assertEqual(ctx.exception.status_code, 400)

        after = sorted(d.name for d in (_evalset_root / "golden").iterdir())
        self.assertEqual(after, before, "拒绝后 golden 目录不得有任何变化")

    def test_git_failure_rolls_back_everything(self):
        """git 提交失败 → 文件删、登记删、revision 回旧值（AC-007 失败链路）。"""
        from fastapi import HTTPException

        from app.dataset import api as dataset_api
        from app.dataset import repo as dataset_repo

        before_dirs = sorted(d.name for d in (_evalset_root / "golden").iterdir())
        before_meta_ids = {r["case_id"] for r in dataset_repo.list_by_layer("golden")}
        with _evalset_to_tmp():
            old_rev = dataset_repo.get_golden_revision()

        real_git = dataset_api._git

        def failing_git(repo_root, *args, timeout=30.0):
            if "commit" in args:
                raise RuntimeError("注入的 git 提交失败")
            return real_git(repo_root, *args, timeout=timeout)

        with _evalset_to_tmp(), \
             patch.object(dataset_api, "_git", side_effect=failing_git), \
             patch.object(dataset_api, "_try_git_push", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                dataset_api.create_golden_case(
                    dataset_api.GoldenCaseCreateRequest(demand_md="# 新需求\n内容")
                )
            self.assertEqual(ctx.exception.status_code, 500)

        # 零残留：目录、登记、revision、git index 全部回到新增前
        after_dirs = sorted(d.name for d in (_evalset_root / "golden").iterdir())
        self.assertEqual(after_dirs, before_dirs, "失败后 golden 目录不得残留新 case")
        porcelain = _git(_repo_root, "status", "--porcelain")
        self.assertEqual(porcelain, "", "回滚后 git 工作区与 index 必须干净（staged blob 残留会污染后续提交）")
        after_meta_ids = {r["case_id"] for r in dataset_repo.list_by_layer("golden")}
        self.assertEqual(after_meta_ids, before_meta_ids, "失败后 dataset_meta 不得残留新登记")
        with _evalset_to_tmp():
            self.assertEqual(dataset_repo.get_golden_revision(), old_rev,
                             "失败后锁定 revision 必须回到旧值")

    def test_push_failure_is_soft(self):
        """push 失败不回滚：新增四件套完整 + warning 返回（FR-004 fail-soft，review #8）。"""
        from app.dataset import api as dataset_api

        real_git_for_push_test = dataset_api._git

        def failing_push_git(repo_root, *args, timeout=30.0):
            if "push" in args:
                raise RuntimeError("注入的 push 失败（无凭据）")
            return real_git_for_push_test(repo_root, *args, timeout=timeout)

        with _evalset_to_tmp(), \
             patch.object(dataset_api, "_git", side_effect=failing_push_git):
            resp = dataset_api.create_golden_case(
                dataset_api.GoldenCaseCreateRequest(demand_md="# 新需求（push 失败场景）")
            )

        self.assertTrue((_evalset_root / "golden" / resp["case_id"] / "demand.md").exists(),
                        "push 失败不得回滚已生效的新增")
        self.assertIsNotNone(resp["git_push_warning"], "push 失败必须返回 warning")
        self.assertIn("push", resp["git_push_warning"])

    def test_selfheal_audit_repo_without_parent_git(self):
        """生产容器态（evalset 无上级 git 仓库）：自愈初始化独立审计仓库，新增照常可用。"""
        import tempfile as _tf

        from app.dataset import api as dataset_api

        bare_root = Path(_tf.mkdtemp()) / "evalset"
        (bare_root / "golden" / "case-001").mkdir(parents=True)
        (bare_root / "golden" / "case-001" / "demand.md").write_text("# 基线", encoding="utf-8")

        with patch("app.common.evalset.evalset_root", return_value=bare_root),              patch.object(dataset_api, "_try_git_push", return_value=None):
            resp = dataset_api.create_golden_case(
                dataset_api.GoldenCaseCreateRequest(demand_md="# 容器态新增")
            )

        self.assertTrue((bare_root / ".git").exists(), "无上级仓库时应自愈 init 独立审计仓库")
        head_msg = _git(bare_root, "log", "-1", "--pretty=%s")
        self.assertIn(resp["case_id"], head_msg, "新增必须落在审计仓库历史里")

    def test_case_id_autonumber(self):
        """编号接现有最大值（连续两次新增编号递增）。"""
        import re

        nums = [
            int(m.group(1))
            for d in (_evalset_root / "golden").iterdir()
            if (m := re.fullmatch(r"case-(\d+)", d.name))
        ]
        resp = self._create("# 需求A\nx")
        self.assertEqual(resp["case_id"], f"case-{max(nums) + 1:03d}")
        resp2 = self._create("# 需求B\ny")
        self.assertEqual(resp2["case_id"], f"case-{max(nums) + 2:03d}")


if __name__ == "__main__":
    unittest.main()
