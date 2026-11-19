"""Initialize one isolated Harness repository and bind its initial release."""
import json
import os
import tempfile
from pathlib import Path

from app.core.git_ops import current_commit, init_work_repo, commit_registry_metadata_and_push
from app.core.settings import settings


def initialize(seed: Path, agent: str) -> None:
    from contracts.creative_harness import build_agent_package

    root = settings.harness_work_dir_path
    root.parent.mkdir(parents=True, exist_ok=True)
    if not root.exists():
        with tempfile.TemporaryDirectory(dir=root.parent) as tmp:
            package = Path(tmp) / "repo"
            build_agent_package(seed, package, agent)
            (package / "registry.json").unlink(missing_ok=True)
            package.rename(root)
    manifest = json.loads((root / "architecture.json").read_text(encoding="utf-8"))
    if [item["name"] for item in manifest["agents"] if item["role"] == "main"] != [agent]:
        raise RuntimeError("已有 Harness 工作区与本实例不匹配，请核对挂载卷。")
    init_work_repo()
    registry = root / "registry.json"
    if not registry.exists():
        record = {"version": 1, "commit_hash": current_commit(), "change_summary": f"{agent} initial release"}
        registry.write_text(json.dumps({"schema_version": 1, "production": 1, "versions": [record],
                                        "rollback_log": []}, indent=2), encoding="utf-8")
    # If interrupted after writing the registry, the next boot finishes its commit.
    from app.core.git_ops import _git
    if _git(["status", "--porcelain", "--", "registry.json"], root):
        commit_registry_metadata_and_push("Register initial isolated Harness")


if __name__ == "__main__":
    initialize(Path("/app/harness_seed/repo"), os.environ["WRITING_AGENT_ID"])
