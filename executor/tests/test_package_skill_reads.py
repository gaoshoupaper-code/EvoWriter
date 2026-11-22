import sqlite3
from contextlib import closing

from langgraph.checkpoint.sqlite import SqliteSaver


def test_role_skill_receipts_survive_checkpoint_reopen_and_do_not_cross_tasks(tmp_path):
    from app.domains.writing.package_skill_reads import PackageSkillReads

    path = tmp_path / "checkpoint.sqlite3"
    with SqliteSaver.from_conn_string(str(path)) as saver:
        saver.setup()
        reads = PackageSkillReads(path, "instruction-1", "a" * 40)
        assert reads.restore("text-subagent") == set()
        reads.record("text-subagent", "Pause", "1" * 64)
        reads.record("text-subagent", "Pause", "1" * 64)

    reopened = PackageSkillReads(path, "instruction-1", "a" * 40)
    assert reopened.restore("text-subagent") == {("Pause", "1" * 64)}
    assert reopened.restore("review-subagent") == set()
    assert PackageSkillReads(path, "instruction-2", "a" * 40).restore("text-subagent") == set()
    assert PackageSkillReads(path, "instruction-1", "b" * 40).restore("text-subagent") == set()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT count(*) FROM package_skill_reads").fetchone()[0] == 1
