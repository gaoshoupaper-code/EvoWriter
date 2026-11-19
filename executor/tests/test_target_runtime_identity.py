"""Run the candidate preparation path in a disposable Linux process."""
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def _check_isolated_snapshot(root):
    from app.domains.writing import target_tasks
    from app.platform.agent import runtime_identity
    from app.platform.core.settings import Settings
    from contracts.target_tasks import TargetTaskRequest

    root = Path(root)
    store = target_tasks.TargetTaskStore(root / "tasks")
    request = TargetTaskRequest.model_validate({
        "task_id": "sample", "attempt_id": "one", "source_commit": "a" * 40,
        "target": {"task_type": "chapter_text", "volume_ids": [1], "chapter": 1},
        "input": {"confirmation_rules": "test"},
    })
    store.create(request, metadata={"workspace": str(root / "workspace")})
    package = store.package_directory(request.task_id)
    package.mkdir(parents=True)
    executor_root = Path(runtime_identity.__file__).resolve().parents[3]
    expected_code = runtime_identity._tree_digest(executor_root)
    os.environ["EXECUTOR_IMAGE_ID"] = "synthetic-image"
    os.environ["WRITING_AGENT_ID"] = "text"
    fake_model = SimpleNamespace(openai_api_base="http://127.0.0.1:12345")
    with patch("app.platform.core.settings.get_settings", return_value=Settings.model_construct()), \
         patch.object(target_tasks, "build_frozen_model", return_value=fake_model):
        target_tasks._prepare_candidate(store, request.task_id, 1)
        with unittest.TestCase().assertRaises(PermissionError):
            (executor_root / "pyproject.toml").read_bytes()
        with unittest.TestCase().assertRaises(PermissionError):
            store.trusted_path(request.task_id).read_bytes()
        identity = runtime_identity.build_runtime_identity(harness_root=package, harness_commit=request.source_commit)
        assert identity["executor_code_digest"] == expected_code
        assert identity["executor_image_id"] == "synthetic-image"
        assert identity["harness_commit"] == request.source_commit
        assert len(identity["dependency_lock_digest"]) == len(identity["identity_digest"]) == 64
        assert "EXECUTOR_IMAGE_ID" not in os.environ


@unittest.skipUnless(sys.platform == "linux", "requires Linux candidate isolation")
class CandidateSnapshotTest(unittest.TestCase):
    def test_identity_survives_candidate_isolation(self):
        with tempfile.TemporaryDirectory() as root:
            worker = multiprocessing.get_context("spawn").Process(target=_check_isolated_snapshot, args=(root,))
            worker.start()
            worker.join(30)
            if worker.is_alive():
                worker.kill()
                worker.join()
            self.assertEqual(worker.exitcode, 0)
