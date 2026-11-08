"""judge 触发时选择测试（REQ-20260920-104714 / FR-003 / AC-004、AC-005）。

覆盖：
- 候选列表：eval + evolution 全部配置、排除 executor（AC-004）
- 默认解析：未选择时 default 为 eval 激活项（AC-004）
- 指定配置触发：批次 judge_fp 为该配置指纹（AC-004）
- 无效配置拒绝：所选配置不存在 / 缺 key → ValueError（AC-005，API 层映射 400）
- 同源标记：与 executor 被测模型同家族的候选被标记（AC-005 界面黄条数据源）
- score_case 透传 config_id 到 llm.chat（FR-003 评分链路）
"""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# LlmConfigsRepository.create 加密 api_key 需要 master key（模块级设置一次即可，
# 不与 EVOLUTION_DB 的「后 import 覆盖」问题相关）
os.environ["EVOLUTION_MASTER_KEY"] = "a" * 64
os.environ["EXECUTOR_URL"] = "http://127.0.0.1:0"


class JudgeTestBase(unittest.TestCase):
    """每类独立 DB（注入连接，免疫 EVOLUTION_DB 被「后 import 模块」覆盖——
    与 test_benchmark_concurrency 同模式；模块级 init_db 会让多个测试文件
    建到同一个库上，llm_configs 数据互相穿插）。"""

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
        # 还原环境变量：后跑的模块级 init_db（如 test_fr007）会按它打开库，
        # 残留我们的 tmp 路径会让它读到本模块种下的 llm_configs
        if self._prev_db_env is None:
            os.environ.pop("EVOLUTION_DB", None)
        else:
            os.environ["EVOLUTION_DB"] = self._prev_db_env


def _seed_config(db, scope, model="deepseek-chat", active=True):
    cfg_id = db.LlmConfigsRepository.create(
        name=f"{scope}-cfg-{model}", api_key="sk-test", base_url="http://x",
        model=model, scope=scope,
    )
    if not active:
        self.db.execute("UPDATE llm_configs SET is_active=0 WHERE id=?", (cfg_id,))
    return cfg_id


class JudgeCandidatesTest(JudgeTestBase):
    """AC-004：候选列表范围与同源标记。"""

    def setUp(self):
        super().setUp()
        self.db.execute("DELETE FROM llm_configs")

    def test_candidates_include_eval_evolution_exclude_executor(self):
        from app.benchmark import api as bench_api
        from app.benchmark import manifest as bench_manifest

        exec_id = _seed_config(self.db, "executor", model="deepseek-chat")
        eval_id = _seed_config(self.db, "eval", model="glm-4.7")
        evo_id = _seed_config(self.db, "evolution", model="kimi-k2")

        with patch.object(bench_manifest, "fetch_platform_binding"), \
             patch("app.benchmark.api.repo"):
            resp = bench_api.list_judges()

        models = {j["model"] for j in resp["judges"]}
        ids = {j["config_id"] for j in resp["judges"]}
        self.assertIn("glm-4.7", models, "eval 配置应在候选中")
        self.assertIn("kimi-k2", models, "evolution 配置应在候选中")
        self.assertNotIn(exec_id, ids, "executor 配置不得出现在候选中")

    def test_default_is_eval_active(self):
        from app.benchmark import api as bench_api

        _seed_config(self.db, "eval", model="glm-4.7")
        _seed_config(self.db, "evolution", model="kimi-k2")

        with patch("app.benchmark.api.repo"):
            resp = bench_api.list_judges()

        self.assertEqual(resp["default"]["scope"], "eval")
        self.assertEqual(resp["default"]["model"], "glm-4.7")
        self.assertFalse(resp["default"]["degraded"])

    def test_same_family_flag(self):
        from app.benchmark import api as bench_api

        _seed_config(self.db, "executor", model="deepseek-chat")
        _seed_config(self.db, "eval", model="glm-4.7")
        _seed_config(self.db, "evolution", model="deepseek-v3")

        with patch("app.benchmark.api.repo"):
            resp = bench_api.list_judges()

        flags = {j["model"]: j["same_family_as_executor"] for j in resp["judges"]}
        self.assertTrue(flags["deepseek-v3"], "与 executor 同家族应标记 True")
        self.assertFalse(flags["glm-4.7"], "异家族应为 False")


class JudgeSelectionTest(JudgeTestBase):
    """退役后，任何评委配置均不得重新打开旧版批次。"""

    def test_valid_group_cannot_reopen_legacy_runner(self):
        from app.benchmark import runner
        cfg = _seed_config(self.db, "eval", model="kimi-k2")
        group = self.db.JudgeGroupsRepository.create(name="保留评委组", member_config_ids=[cfg])
        with patch.object(runner, "_dispatch_batch") as dispatch:
            with self.assertRaisesRegex(RuntimeError, "退役"):
                runner.trigger_run(versions=[1], case_ids=["case-a"], judge_group_id=group)
        dispatch.assert_not_called()
        self.assertIsNotNone(self.db.JudgeGroupsRepository.get(group))
        self.assertEqual(self.db.query_one("SELECT COUNT(*) AS n FROM benchmark_runs")["n"], 0)

    def test_missing_or_invalid_group_cannot_bypass_retirement(self):
        from app.benchmark import runner
        for group in (None, 999):
            with self.subTest(group=group), self.assertRaisesRegex(RuntimeError, "退役"):
                runner.trigger_run(versions=[1], case_ids=["case-a"], judge_group_id=group)

    def test_snapshot_cannot_bypass_retirement(self):
        from app.benchmark import runner
        with self.assertRaisesRegex(RuntimeError, "退役"):
            runner.trigger_run(versions=[1], case_ids=["case-a"], group_snapshot={"members": []})


class ScoreChainTest(JudgeTestBase):
    """FR-003：score_case 透传成员 config_id 到 llm.chat（维×judge 调用均携带）。"""

    def test_score_case_passes_config_id(self):
        import json

        from app.benchmark import scorer

        deliveries = {"主线 storyline": "x" * 300, "人物 character": "y" * 300, "世界观 worldview": "z" * 300}
        # 单维契约（rubric v4）：score + 两段理由
        raw = json.dumps({"score": 5, "达标": ["承诺点全部兑现"], "不足": ["未发现不足"]})
        judges = [{"config_id": 42, "name": "选中项", "model": "m", "fingerprint": "f"}]
        with patch.object(scorer.llm, "chat", return_value=raw) as m:
            scorer.score_case("demand", deliveries, judges)

        self.assertEqual(m.call_count, 5, "五维应各触发一次 judge 调用")
        for call in m.call_args_list:
            self.assertEqual(call.kwargs.get("config_id"), 42,
                             "score_case 应把成员 config_id 透传给每次 llm.chat")


if __name__ == "__main__":
    unittest.main()
