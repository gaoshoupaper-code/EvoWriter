"""platform_ledger 测试（系统资产页数据源切 Platform 账本）。

覆盖：
- fetch：正常解析 / 不可达 / 非 200 / 响应畸形（宁拒勿错，502 语义）
- 账本条目富化：status 标记、change_summary 取 note、倒序
- same_code_as：同 commit 更早版本标注（最早持有者为基准）
- based_on：git first-parent 祖先链上最近账本版本；root / error 状态
- resolve_commit：版本→commit 解析，不存在返回 None
- TTL 缓存：窗口内不重复拉账本
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.versioning import platform_ledger


def _ledger():
    """模拟账本流水：对应线上 v6–v14 形态（含同 commit 重复与倒挂）。"""
    return {
        "items": [
            {"version": 6, "commit": "c6", "note": "根版本", "created_at": "2026-07-20"},
            {"version": 7, "commit": "c7", "note": "", "created_at": "2026-08-01"},
            {"version": 8, "commit": "c8", "note": "修复拦截", "created_at": "2026-08-02"},
            {"version": 9, "commit": "c8", "note": "Phase A 验证", "created_at": "2026-09-19"},
            {"version": 10, "commit": "c10", "note": "架构切换", "created_at": "2026-09-20"},
            {"version": 11, "commit": "c8", "note": "回滚演练", "created_at": "2026-09-20"},
            {"version": 12, "commit": "c10", "note": "演练结束", "created_at": "2026-09-20"},
            {"version": 13, "commit": "c13", "note": "", "created_at": "2026-09-22"},
            {"version": 14, "commit": "c14", "note": "", "created_at": "2026-09-22"},
        ],
        "production_version": 14,
    }


class _Resp:
    """httpx.Response 替身。"""

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else _ledger()

    def json(self):
        return self._payload


class FetchTest(unittest.TestCase):
    def setUp(self):
        platform_ledger.clear_cache()

    def test_ok_sorted_desc_with_status(self):
        with patch.object(platform_ledger.httpx, "get", return_value=_Resp()):
            versions = platform_ledger.list_versions()
        self.assertEqual([v["version"] for v in versions],
                         [14, 13, 12, 11, 10, 9, 8, 7, 6])
        by_ver = {v["version"]: v for v in versions}
        self.assertEqual(by_ver[14]["status"], "production")
        self.assertEqual(by_ver[13]["status"], "retired")
        # note → change_summary，空 note 容忍为空串
        self.assertEqual(by_ver[8]["change_summary"], "修复拦截")
        self.assertEqual(by_ver[13]["change_summary"], "")

    def test_unreachable_raises(self):
        with patch.object(platform_ledger.httpx, "get",
                          side_effect=httpx.ConnectError("boom")):
            with self.assertRaises(RuntimeError):
                platform_ledger.list_versions()

    def test_non_200_raises(self):
        with patch.object(platform_ledger.httpx, "get", return_value=_Resp(status_code=500)):
            with self.assertRaises(RuntimeError):
                platform_ledger.list_versions()

    def test_malformed_raises(self):
        with patch.object(platform_ledger.httpx, "get", return_value=_Resp(payload={"items": "x"})):
            with self.assertRaises(RuntimeError):
                platform_ledger.list_versions()

    def test_resolve_commit(self):
        with patch.object(platform_ledger.httpx, "get", return_value=_Resp()):
            self.assertEqual(platform_ledger.resolve_commit(13), "c13")
            self.assertIsNone(platform_ledger.resolve_commit(99))
            self.assertEqual(platform_ledger.production_version_number(), 14)

    def test_ttl_cache_one_fetch(self):
        calls = {"n": 0}

        def _fake_get(*_a, **_kw):
            calls["n"] += 1
            return _Resp()

        with patch.object(platform_ledger.httpx, "get", side_effect=_fake_get):
            platform_ledger.list_versions()
            platform_ledger.list_versions()
            platform_ledger.resolve_commit(8)
        self.assertEqual(calls["n"], 1)


class SameCodeTest(unittest.TestCase):
    def setUp(self):
        platform_ledger.clear_cache()

    def test_same_code_marks_earliest_holder(self):
        with patch.object(platform_ledger.httpx, "get", return_value=_Resp()):
            versions = {v["version"]: v for v in platform_ledger.list_versions()}
        # v9/v11 与 v8 同 commit → 标 v8；v12 与 v10 同 → 标 v10；持有者自身无标注
        self.assertEqual(versions[9]["same_code_as"], 8)
        self.assertEqual(versions[11]["same_code_as"], 8)
        self.assertEqual(versions[12]["same_code_as"], 10)
        self.assertIsNone(versions[8]["same_code_as"])
        self.assertIsNone(versions[10]["same_code_as"])
        self.assertIsNone(versions[14]["same_code_as"])


class BasedOnTest(unittest.TestCase):
    """祖先解析：_git_rev_list 打桩，纯映射逻辑验证。

    形态对齐线上实测：v8←v7、v10←v8、v14←v10、v13←v14（倒挂）、v6 根。
    """

    ANCESTRY = {
        "c7": ["c6"],                     # v7 基于 v6
        "c8": ["c7", "c6"],               # v8 基于 v7
        "c10": ["c8", "c7", "c6"],        # v10 基于 v8（跳过 v9：同 commit 不算祖先目标）
        "c13": ["c14", "c10", "c8", "c7", "c6"],  # v13 基于 v14（版本号倒挂）
        "c14": ["c10", "c8", "c7", "c6"],  # v14 基于 v10
    }

    def setUp(self):
        platform_ledger.clear_cache()

    def _versions(self):
        with patch.object(platform_ledger.httpx, "get", return_value=_Resp()), \
             patch.object(platform_ledger, "_git_rev_list",
                          side_effect=lambda c: self.ANCESTRY.get(c, [])):
            return {v["version"]: v for v in platform_ledger.list_versions()}

    def test_based_on_chain(self):
        versions = self._versions()
        self.assertEqual(versions[7]["based_on"], 6)
        self.assertEqual(versions[8]["based_on"], 7)
        self.assertEqual(versions[10]["based_on"], 8)
        self.assertEqual(versions[13]["based_on"], 14)
        self.assertEqual(versions[14]["based_on"], 10)
        # 全部 resolved，root 仅 v6
        self.assertEqual(versions[6]["based_on_status"], "root")
        for v in (7, 8, 10, 13, 14):
            self.assertEqual(versions[v]["based_on_status"], "resolved")

    def test_same_code_entry_has_no_based_on(self):
        # v9 与 v8 同 commit：same_code_as 表达归属，based_on 不参与
        versions = self._versions()
        self.assertEqual(versions[9]["same_code_as"], 8)
        self.assertIsNone(versions[9]["based_on"])

    def test_git_error_marks_error_status(self):
        def _boom(_c):
            raise RuntimeError("git failed")

        with patch.object(platform_ledger.httpx, "get", return_value=_Resp()), \
             patch.object(platform_ledger, "_git_rev_list", side_effect=_boom):
            versions = {v["version"]: v for v in platform_ledger.list_versions()}
        for v in versions.values():
            if v["same_code_as"] is None:
                self.assertEqual(v["based_on_status"], "error")
                self.assertIsNone(v["based_on"])

    def test_unknown_commit_in_ancestry_skipped(self):
        # 祖先链含非账本 commit（cX），最近账本祖先仍是链上第一个命中的版本
        ancestry = {"c8": ["cX", "c7", "c6"]}
        with patch.object(platform_ledger.httpx, "get", return_value=_Resp()), \
             patch.object(platform_ledger, "_git_rev_list", side_effect=lambda c: ancestry.get(c, [])):
            versions = {v["version"]: v for v in platform_ledger.list_versions()}
        self.assertEqual(versions[8]["based_on"], 7)


class AncestryMemoTest(unittest.TestCase):
    """rev-list 按 commit 永久 memo：重复富化不重 fork git（review r1）。"""

    def setUp(self):
        platform_ledger.clear_cache()

    def test_rev_list_memoized_per_commit(self):
        from app.core import git_ops
        calls = {"n": 0}

        def _fake_git(args, cwd):
            calls["n"] += 1
            self.assertEqual(args[:2], ["rev-list", "--first-parent"])
            return "child parent root"

        with patch.object(git_ops, "_git", side_effect=_fake_git):
            first = platform_ledger._git_rev_list("abc123")
            second = platform_ledger._git_rev_list("abc123")
            other = platform_ledger._git_rev_list("def456")
        self.assertEqual(first, ["child", "parent", "root"])
        self.assertEqual(second, first)
        self.assertEqual(other, ["child", "parent", "root"])
        self.assertEqual(calls["n"], 2)  # 两个 commit 各 fork 一次，重复调用命中 memo


if __name__ == "__main__":
    unittest.main()
