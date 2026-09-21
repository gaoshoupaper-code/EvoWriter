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
    """AC-004/005：触发时指定 judge 的指纹与无效拒绝。"""

    def setUp(self):
        super().setUp()
        self.db.execute("DELETE FROM llm_configs")

    def _trigger(self, **kwargs):
        """调 runner.trigger_run（mock 掉后台派发与 golden 依赖）。"""
        from app.benchmark import runner

        with patch.object(runner, "_dispatch_batch") as mock_dispatch, \
             patch.object(runner, "_get_production_version", return_value=None):
            batch_id = runner.trigger_run(versions=[1], case_ids=["case-a"], **kwargs)
        return batch_id, mock_dispatch.call_args

    def test_selected_judge_fingerprint_recorded(self):
        """选择必须真实生效：seed 两个配置，选非默认的，断言指纹=所选且≠默认
        （只 seed 一个时所选与默认恒等，参数被静默丢弃也测不出——review #4）。"""
        from app.benchmark import manifest as bench_manifest

        _seed_config(self.db, "eval", model="glm-4.7")   # 首条，自动激活=默认
        cfg_id = _seed_config(self.db, "eval", model="kimi-k2")  # 第二条，非激活

        batch_id, dispatch_args = self._trigger(judge_config_id=cfg_id)
        self.assertEqual(dispatch_args.args[2], cfg_id,
                         "judge_config_id 必须透传到批次派发（FR-003 链路）")

        selected_fp = bench_manifest.resolve_judge_config(cfg_id)["fingerprint"]
        default_fp = bench_manifest.resolve_judge_config()["fingerprint"]
        self.assertNotEqual(selected_fp, default_fp, "前置：所选与默认必须是不同配置")
        rows = self.db.query_all("SELECT judge_fp FROM benchmark_runs WHERE batch_id=?", (batch_id,))
        self.assertTrue(rows)
        self.assertEqual({r["judge_fp"] for r in rows}, {selected_fp},
                         "批次应记录所选 judge 的指纹（而非默认解析）")

    def test_default_judge_when_not_selected(self):
        from app.benchmark import manifest as bench_manifest
        from app.benchmark import repo as bench_repo

        _seed_config(self.db, "eval", model="glm-4.7")

        batch_id, _ = self._trigger()

        expected_fp = bench_manifest.resolve_judge_config()["fingerprint"]
        rows = self.db.query_all("SELECT judge_fp FROM benchmark_runs WHERE batch_id=?", (batch_id,))
        self.assertEqual({r["judge_fp"] for r in rows}, {expected_fp},
                         "未选择时应记录默认解析 judge 的指纹")

    def test_nonexistent_config_rejected(self):
        from app.benchmark import runner

        _seed_config(self.db, "eval", model="glm-4.7")
        with self.assertRaises(ValueError) as ctx:
            self._trigger(judge_config_id=9999)
        self.assertIn("9999", str(ctx.exception), "错误信息应指明配置 ID")

    def test_executor_scope_config_rejected(self):
        """DEC-010 判评分离：executor 生产模型配置不得被选为 judge（review #9）。"""
        exec_id = _seed_config(self.db, "executor", model="deepseek-chat")
        _seed_config(self.db, "eval", model="glm-4.7")

        with self.assertRaises(ValueError) as ctx:
            self._trigger(judge_config_id=exec_id)
        self.assertIn(str(exec_id), str(ctx.exception))

    def test_empty_base_url_or_model_rejected(self):
        """AC-005：base_url / model 为空同样拒绝（此前只测了缺 key，review #10）。"""
        _seed_config(self.db, "eval", model="glm-4.7")
        for field in ("base_url", "model"):
            with self.subTest(field=field):
                cfg_id = _seed_config(self.db, "eval", model="kimi-k2")
                self.db.execute(
                    f"UPDATE llm_configs SET {field}='' WHERE id=?", (cfg_id,)
                )
                with self.assertRaises(ValueError):
                    self._trigger(judge_config_id=cfg_id)

    def test_trigger_spawns_worker_without_event_loop(self):
        """review P0 回归：sync 上下文（无 event loop）直接调 trigger_run 不得炸，
        且派发线程参数完整（并发度 + judge_config_id）。"""
        from unittest.mock import patch

        from app.benchmark import runner

        _seed_config(self.db, "eval", model="glm-4.7")
        started = threading.Event()
        captured: dict = {}

        def fake_dispatch(batch_id, concurrency, judge_config_id):
            captured.update(batch_id=batch_id, concurrency=concurrency,
                            judge_config_id=judge_config_id)
            started.set()

        with patch.object(runner, "_dispatch_batch", fake_dispatch),              patch.object(runner, "_get_production_version", return_value=None):
            batch_id = runner.trigger_run(versions=[1], case_ids=["case-a"])

        self.assertTrue(started.is_set(), "trigger_run 必须在无 event loop 的同步上下文里完成派发")
        self.assertEqual(captured["batch_id"], batch_id)
        self.assertEqual(captured["concurrency"], 3)
        self.assertIsNone(captured["judge_config_id"])

    def test_config_without_key_rejected(self):
        from app.benchmark import runner

        cfg_id = _seed_config(self.db, "eval", model="glm-4.7")
        self.db.execute("UPDATE llm_configs SET api_key_enc=NULL WHERE id=?", (cfg_id,))

        with self.assertRaises(ValueError) as ctx:
            self._trigger(judge_config_id=cfg_id)
        self.assertIn(str(cfg_id), str(ctx.exception))


class ScoreChainTest(JudgeTestBase):
    """FR-003：score_case 透传 config_id 到 llm.chat（五次按维调用均携带）。"""

    def test_score_case_passes_config_id(self):
        import json

        from app.benchmark import scorer

        deliveries = {"主线 storyline": "x" * 300, "人物 character": "y" * 300, "世界观 worldview": "z" * 300}
        # 单维契约（rubric v4）：score + 两段理由
        raw = json.dumps({"score": 5, "达标": ["承诺点全部兑现"], "不足": ["未发现不足"]})
        with patch.object(scorer.llm, "chat", return_value=raw) as m:
            scorer.score_case("demand", deliveries, judge_config_id=42)

        self.assertEqual(m.call_count, 5, "五维应各触发一次 judge 调用")
        for call in m.call_args_list:
            self.assertEqual(call.kwargs.get("config_id"), 42,
                             "score_case 应把 judge_config_id 透传给每次 llm.chat")


if __name__ == "__main__":
    unittest.main()
