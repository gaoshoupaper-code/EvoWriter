"""push_mirror 行为测试（harness 镜像推送，发版后自动备份到外部 git 仓库）。

覆盖：未配置跳过、推送 refspec 形状、non-fast-forward 强制对齐、
ssh key 注入 GIT_SSH_COMMAND。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import git_ops  # noqa: E402

MIRROR_URL = "git@github.com:example/evowriter.git"


class PushMirrorTest(unittest.TestCase):
    def test_disabled_when_no_remote_configured(self) -> None:
        """未配置 mirror remote → 返回 None，不执行任何 git 命令。"""
        with patch.object(git_ops.settings, "harness_mirror_remote_url", ""), patch.object(
            git_ops, "_git"
        ) as git_run:
            self.assertIsNone(git_ops.push_mirror())
        git_run.assert_not_called()

    def test_pushes_bare_main_to_mirror_branch(self) -> None:
        """推送形状：bare repo 上 push main:refs/heads/<mirror_branch>。"""
        with patch.object(git_ops.settings, "harness_mirror_remote_url", MIRROR_URL), patch.object(
            git_ops.settings, "harness_mirror_branch", "harness/production"
        ), patch.object(
            git_ops.settings, "harness_mirror_ssh_key_path", ""
        ), patch.object(
            git_ops, "_git", side_effect=["", "abc123def456"]
        ) as git_run:
            result = git_ops.push_mirror()

        self.assertEqual(result, "abc123def456")
        push_call = git_run.call_args_list[0]
        self.assertEqual(
            push_call.args[0], ["push", MIRROR_URL, "main:refs/heads/harness/production"]
        )
        self.assertEqual(push_call.args[1], git_ops.read_dir())
        self.assertEqual(push_call.kwargs["timeout"], git_ops._GIT_PUSH_TIMEOUT_SECONDS)
        # 未配 ssh key → 不注入 GIT_SSH_COMMAND（https 认证或默认 agent）
        self.assertIsNone(push_call.kwargs["env"])

    def test_force_aligns_on_non_fast_forward(self) -> None:
        """外部分支分叉（non-fast-forward）→ 强制对齐再推一次。"""
        boom = RuntimeError("git push 失败: hint: Updates were rejected ... non-fast-forward")
        with patch.object(git_ops.settings, "harness_mirror_remote_url", MIRROR_URL), patch.object(
            git_ops.settings, "harness_mirror_ssh_key_path", ""
        ), patch.object(
            git_ops, "_git", side_effect=[boom, "", "force456"]
        ) as git_run:
            result = git_ops.push_mirror()

        self.assertEqual(result, "force456")
        second_push = git_run.call_args_list[1]
        self.assertIn("--force", second_push.args[0])

    def test_other_push_errors_propagate(self) -> None:
        """非 non-fast-forward 失败（网络/认证）→ 直接抛出，由调用方软失败。"""
        boom = RuntimeError("git push 失败: Permission denied (publickey)")
        with patch.object(git_ops.settings, "harness_mirror_remote_url", MIRROR_URL), patch.object(
            git_ops.settings, "harness_mirror_ssh_key_path", ""
        ), patch.object(
            git_ops, "_git", side_effect=boom
        ):
            with self.assertRaises(RuntimeError):
                git_ops.push_mirror()

    def test_ssh_key_injects_git_ssh_command(self) -> None:
        """配置 ssh 私钥 → push 带 GIT_SSH_COMMAND（IdentitiesOnly + accept-new）。"""
        with patch.object(git_ops.settings, "harness_mirror_remote_url", MIRROR_URL), patch.object(
            git_ops.settings, "harness_mirror_ssh_key_path", "/secrets/mirror_key"
        ), patch.object(
            git_ops, "_git", side_effect=["", "ssh123"]
        ) as git_run:
            git_ops.push_mirror()

        env = git_run.call_args_list[0].kwargs["env"]
        cmd = env["GIT_SSH_COMMAND"]
        self.assertIn("-i /secrets/mirror_key", cmd)
        self.assertIn("IdentitiesOnly=yes", cmd)
        self.assertIn("accept-new", cmd)


if __name__ == "__main__":
    unittest.main()
