"""评测组仓库与删除联动测试（REQ-20260930-162207 / FR-001 / AC-001、AC-002）。

覆盖：
- 组 CRUD：创建/列表/改名/改成员/删除（AC-001）
- 成员数边界：0 / 6 拒绝，1 / 5 放行（DEC-004 组合法大小 1~5）
- 判评分离：executor scope 配置不得入组；不存在的配置拒绝（DEC-010 沿用）
- 重名校验：同名组拒绝
- 删除联动：删配置 → 所有组剔除该成员；组空自动删组（DEC-005 / AC-002）
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["EVOLUTION_MASTER_KEY"] = "a" * 64
os.environ["EXECUTOR_URL"] = "http://127.0.0.1:0"


class JudgeGroupTestBase(unittest.TestCase):
    """独立临时 DB（与 test_benchmark_judge_select 同模式）。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self._dbfile = Path(self._tmpdir) / "test.db"
        self._prev_db_env = os.environ.get("EVOLUTION_DB")
        os.environ["EVOLUTION_DB"] = str(self._dbfile)
        import importlib
        import sqlite3

        import app.core.settings as settings_mod

        importlib.reload(settings_mod)
        import app.core.db as db

        conn = sqlite3.connect(self._dbfile, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        db._conn = conn
        db._master_key_cache = None
        db.init_db()
        self.db = db

    def tearDown(self) -> None:
        try:
            self.db.get_conn().close()
        except Exception:
            pass
        if self._prev_db_env is None:
            os.environ.pop("EVOLUTION_DB", None)
        else:
            os.environ["EVOLUTION_DB"] = self._prev_db_env

    def _seed_config(self, scope="eval", model="glm-4.7", name=None) -> int:
        return self.db.LlmConfigsRepository.create(
            name=name or f"{scope}-{model}", api_key="sk-test",
            base_url="http://x", model=model, scope=scope,
        )


class JudgeGroupCrudTest(JudgeGroupTestBase):
    """AC-001：组 CRUD 与成员数校验。"""

    def test_create_and_list_group(self):
        cfg_a = self._seed_config(model="glm-4.7")
        cfg_b = self._seed_config(model="kimi-k2")
        group_id = self.db.JudgeGroupsRepository.create(
            name="三人评审团", member_config_ids=[cfg_a, cfg_b],
        )
        groups = self.db.JudgeGroupsRepository.list_all()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["id"], group_id)
        self.assertEqual(groups[0]["name"], "三人评审团")
        self.assertEqual(
            [m["config_id"] for m in groups[0]["members"]], [cfg_a, cfg_b],
            "成员按传入顺序持久化",
        )

    def test_member_count_bounds(self):
        cfg = self._seed_config()
        with self.assertRaises(ValueError):
            self.db.JudgeGroupsRepository.create(name="空组", member_config_ids=[])
        with self.assertRaises(ValueError):
            self.db.JudgeGroupsRepository.create(
                name="六人组", member_config_ids=[cfg] * 6,
            )
        # 边界合法：1 人组（单评特例）与 5 人组（上限）
        self.db.JudgeGroupsRepository.create(name="一人组", member_config_ids=[cfg])
        five = [self._seed_config(model=f"m{i}") for i in range(5)]
        self.db.JudgeGroupsRepository.create(name="五人组", member_config_ids=five)

    def test_duplicate_members_rejected(self):
        cfg = self._seed_config()
        with self.assertRaises(ValueError):
            self.db.JudgeGroupsRepository.create(
                name="重复成员", member_config_ids=[cfg, cfg],
            )

    def test_duplicate_name_rejected(self):
        cfg = self._seed_config()
        self.db.JudgeGroupsRepository.create(name="评审团", member_config_ids=[cfg])
        with self.assertRaises(ValueError):
            self.db.JudgeGroupsRepository.create(name="评审团", member_config_ids=[cfg])

    def test_unknown_or_executor_member_rejected(self):
        exec_id = self._seed_config(scope="executor", model="deepseek-chat")
        cfg = self._seed_config()
        with self.assertRaises(ValueError):
            self.db.JudgeGroupsRepository.create(
                name="不存在成员", member_config_ids=[9999],
            )
        with self.assertRaises(ValueError):
            self.db.JudgeGroupsRepository.create(
                name="判评不分", member_config_ids=[exec_id],
            )
        # 混入 executor 也拒绝（整组校验，不是只看第一个）
        with self.assertRaises(ValueError):
            self.db.JudgeGroupsRepository.create(
                name="混入", member_config_ids=[cfg, exec_id],
            )

    def test_update_group(self):
        cfg_a = self._seed_config(model="glm-4.7")
        cfg_b = self._seed_config(model="kimi-k2")
        group_id = self.db.JudgeGroupsRepository.create(
            name="旧名", member_config_ids=[cfg_a],
        )
        ok = self.db.JudgeGroupsRepository.update(
            group_id, name="新名", member_config_ids=[cfg_a, cfg_b],
        )
        self.assertTrue(ok)
        group = self.db.JudgeGroupsRepository.get(group_id)
        self.assertEqual(group["name"], "新名")
        self.assertEqual(len(group["members"]), 2)

    def test_delete_group(self):
        cfg = self._seed_config()
        group_id = self.db.JudgeGroupsRepository.create(
            name="待删", member_config_ids=[cfg],
        )
        self.assertTrue(self.db.JudgeGroupsRepository.delete(group_id))
        self.assertIsNone(self.db.JudgeGroupsRepository.get(group_id))
        self.assertEqual(self.db.JudgeGroupsRepository.list_all(), [])
        # 成员配置不受组删除影响
        self.assertIsNotNone(self.db.LlmConfigsRepository.get_safe_by_id(cfg))


class JudgeGroupDeleteLinkageTest(JudgeGroupTestBase):
    """AC-002：删配置联动剔除成员，组空自动删组。"""

    def test_config_delete_removes_member_and_empty_group(self):
        cfg_x = self._seed_config(model="glm-4.7")
        cfg_y = self._seed_config(model="kimi-k2")
        # 组 A 两个成员（删 X 后保留），组 B 仅 X（删 X 后整组消失）
        group_a = self.db.JudgeGroupsRepository.create(
            name="组A", member_config_ids=[cfg_x, cfg_y],
        )
        group_b = self.db.JudgeGroupsRepository.create(
            name="组B", member_config_ids=[cfg_x],
        )
        self.db.LlmConfigsRepository.delete(cfg_x)

        a = self.db.JudgeGroupsRepository.get(group_a)
        self.assertIsNotNone(a, "组A 还有成员 cfg_y，不得被删")
        self.assertEqual([m["config_id"] for m in a["members"]], [cfg_y])
        self.assertIsNone(
            self.db.JudgeGroupsRepository.get(group_b),
            "组B 唯一成员被删，组应自动删除",
        )

    def test_affected_groups_queryable(self):
        """前端删除确认框数据源：组列表可算出受影响组（DEC-005）。"""
        cfg_x = self._seed_config(model="glm-4.7")
        self.db.JudgeGroupsRepository.create(name="组A", member_config_ids=[cfg_x])
        groups_with_x = [
            g["name"] for g in self.db.JudgeGroupsRepository.list_all()
            if any(m["config_id"] == cfg_x for m in g["members"])
        ]
        self.assertEqual(groups_with_x, ["组A"])

    def test_delete_config_not_in_any_group(self):
        cfg = self._seed_config(model="glm-4.7")
        other = self._seed_config(model="kimi-k2")
        self.db.JudgeGroupsRepository.create(name="组", member_config_ids=[other])
        # 删不在任何组里的配置：正常删除，组不受影响
        self.assertTrue(self.db.LlmConfigsRepository.delete(cfg))
        self.assertEqual(len(self.db.JudgeGroupsRepository.list_all()), 1)


class JudgeGroupApiTest(JudgeGroupTestBase):
    """FR-001 API 面：组 CRUD 端点 + 成员详情/同家族标记（DEC-011）。"""

    def test_crud_endpoints(self):
        from app.benchmark import api as bench_api

        cfg = self._seed_config(model="glm-4.7")
        resp = bench_api.create_judge_group(
            bench_api.JudgeGroupCreateRequest(name="评审团", member_config_ids=[cfg])
        )
        group_id = resp["group_id"]

        listing = bench_api.list_judge_groups()
        self.assertEqual(len(listing["groups"]), 1)
        group = listing["groups"][0]
        self.assertEqual(group["name"], "评审团")
        member = group["members"][0]
        self.assertEqual(member["config_id"], cfg)
        self.assertEqual(member["model"], "glm-4.7")
        self.assertFalse(member["stale"])

        ok = bench_api.update_judge_group(
            group_id,
            bench_api.JudgeGroupUpdateRequest(name="改名", member_config_ids=[cfg]),
        )
        self.assertEqual(ok["status"], "updated")
        self.assertEqual(bench_api.list_judge_groups()["groups"][0]["name"], "改名")

        self.assertEqual(
            bench_api.delete_judge_group(group_id)["status"], "deleted"
        )
        self.assertEqual(bench_api.list_judge_groups()["groups"], [])

    def test_create_invalid_returns_400(self):
        from fastapi import HTTPException

        from app.benchmark import api as bench_api

        with self.assertRaises(HTTPException) as ctx:
            bench_api.create_judge_group(
                bench_api.JudgeGroupCreateRequest(name="空组", member_config_ids=[])
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_member_same_family_flag(self):
        """DEC-011：成员逐个带同家族标记（组页黄条数据源）。"""
        from app.benchmark import api as bench_api

        self._seed_config(scope="executor", model="deepseek-chat")
        same = self._seed_config(model="deepseek-v3")
        other = self._seed_config(model="glm-4.7")
        bench_api.create_judge_group(
            bench_api.JudgeGroupCreateRequest(name="混合", member_config_ids=[same, other])
        )
        members = bench_api.list_judge_groups()["groups"][0]["members"]
        flags = {m["model"]: m["same_family_as_executor"] for m in members}
        self.assertTrue(flags["deepseek-v3"])
        self.assertFalse(flags["glm-4.7"])


class GoldenRerunDefaultGroupTest(JudgeGroupTestBase):
    """golden 升级重跑的默认组 = 最近批次组快照（TD of 162207）。"""

    def _snapshot(self, group_id, name="评审团"):
        return {
            "group_id": group_id, "group_name": name,
            "members": [{"config_id": 1, "name": "a", "model": "m", "fingerprint": "f1"}],
        }

    def test_no_history_rejected(self):
        from app.benchmark import repo as bench_repo
        from app.benchmark import runner

        with patch.object(bench_repo, "get_recent_versions", return_value=[1]), \
             self.assertRaises(ValueError) as ctx:
            runner.trigger_golden_upgrade_rerun(k=1)
        self.assertIn("评测组", str(ctx.exception))

    def test_uses_latest_batch_snapshot(self):
        from app.benchmark import repo as bench_repo
        from app.benchmark import runner

        self.db.JudgeGroupsRepository  # noqa: B018 - 触发 db 模块加载
        with patch.object(bench_repo, "get_recent_versions", return_value=[2]), \
             patch.object(bench_repo, "get_latest_judge_snapshot",
                          return_value=self._snapshot(3)), \
             patch.object(runner.dataset_repo, "get_golden_case_ids",
                          return_value=["case-a"]), \
             patch.object(runner.dataset_repo, "get_golden_revision",
                          return_value="rev-test"), \
             patch.object(runner, "_dispatch_batch") as dispatch, \
             patch.object(runner.bench_repo, "create_batch",
                          return_value="batch-x") as mock_create:
            runner.trigger_golden_upgrade_rerun(k=1)

        snapshot = dispatch.call_args.args[2]
        self.assertEqual(snapshot["group_id"], 3)
        self.assertTrue(mock_create.called)


class JudgeGateWiringTest(JudgeGroupTestBase):
    """DEC-009：批次派发时创建 judge 并发闸门（上限常量 15）。"""

    def test_run_batch_passes_gate_to_workers(self):
        import threading

        from app.benchmark import repo as bench_repo
        from app.benchmark import runner

        batch_id = bench_repo.create_batch(
            case_ids=["case-a"], versions=[1], golden_revision="r",
            seeds=1, rubric_version="v4", judge_fp="fp",
        )
        captured: dict = {}

        def fake_execute(row, group_snapshot=None, *, judge_gate=None, **_):
            captured["gate"] = judge_gate

        with patch.object(runner, "_execute_one", fake_execute):
            runner._run_batch_sync(batch_id, concurrency=1)

        self.assertIsInstance(captured["gate"], threading.Semaphore)
        self.assertEqual(
            runner.JUDGE_MAX_CONCURRENCY, 15,
            "闸门上限须与改造前峰值一致（3 worker × 5 维）",
        )


if __name__ == "__main__":
    unittest.main()
