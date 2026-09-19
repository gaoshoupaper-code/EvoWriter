"""runtime_identity 测试：无 git 容器（FR-003 去 git 化）下的身份构建。

Dockerfile.executor 已移除 git 二进制——_git_value 的 subprocess 必须把
FileNotFoundError 吞成空串语义，否则 reload 端点 500 / A/B worker 崩溃 /
run_snapshot 降级（review finding #1）。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app.platform.agent.runtime_identity import build_runtime_identity


class NoGitEnvironmentTest(unittest.TestCase):
    def test_build_identity_without_git_binary(self) -> None:
        """git 二进制不存在（FileNotFoundError）→ 身份正常返回，dirty=False。"""

        def _no_git(*_args, **_kwargs):
            raise FileNotFoundError("git not found in container")

        root = Path(__file__).parent  # 任意存在的目录即可
        with patch(
            "app.platform.agent.runtime_identity.subprocess.run", side_effect=_no_git,
        ):
            identity = build_runtime_identity(harness_root=root, harness_commit="a" * 40)

        self.assertEqual(identity["harness_commit"], "a" * 40)
        self.assertFalse(identity["harness_dirty"])  # 空串 → False
        self.assertTrue(identity["identity_digest"])
        # 未显式传 commit 时同样不崩，commit 退化为空串
        with patch(
            "app.platform.agent.runtime_identity.subprocess.run", side_effect=_no_git,
        ):
            identity2 = build_runtime_identity(harness_root=root)
        self.assertEqual(identity2["harness_commit"], "")
        self.assertFalse(identity2["harness_dirty"])


if __name__ == "__main__":
    unittest.main()
