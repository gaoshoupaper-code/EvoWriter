"""Read receipts for package skills survive reassembly in the task's checkpoint DB."""
import sqlite3
from contextlib import closing
from pathlib import Path


class PackageSkillReads:
    def __init__(self, checkpoint: Path, task_id: str, revision: str):
        self.checkpoint = checkpoint
        self.task_id = task_id
        self.revision = revision
        with closing(sqlite3.connect(checkpoint)) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS package_skill_reads (
                task_id TEXT NOT NULL, revision TEXT NOT NULL, role TEXT NOT NULL,
                name TEXT NOT NULL, content_hash TEXT NOT NULL,
                PRIMARY KEY (task_id, revision, role, name, content_hash))""")

    def restore(self, role: str) -> set[tuple[str, str]]:
        with closing(sqlite3.connect(self.checkpoint)) as db:
            return set(db.execute("""SELECT name, content_hash FROM package_skill_reads
                WHERE task_id=? AND revision=? AND role=?""", (self.task_id, self.revision, role)))

    def record(self, role: str, name: str, content_hash: str) -> None:
        with closing(sqlite3.connect(self.checkpoint)) as db:
            db.execute("INSERT OR IGNORE INTO package_skill_reads VALUES (?,?,?,?,?)",
                       (self.task_id, self.revision, role, name, content_hash))
            db.commit()
