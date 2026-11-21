"""Storage for one chapter-text assessment. Fixed writing inputs live in the case bank."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from app.core import db
from app.core.settings import settings


def init_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS assessment_entries (
        assessment_id TEXT NOT NULL, category TEXT NOT NULL, entry_id TEXT NOT NULL,
        round_number INTEGER NOT NULL, version TEXT, payload TEXT NOT NULL,
        PRIMARY KEY(assessment_id, category, entry_id))""")


def artifact_directory(run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("评测编号无效")
    return settings.db_path.parent / "assessments" / run_id / "artifacts"


def _store_text(directory: Path, text: str) -> str:
    data = text.encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    dest = directory / digest
    if not dest.is_file():
        directory.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            os.replace(temp, dest)
        finally:
            Path(temp).unlink(missing_ok=True)
    return digest


def _take(source, keys):
    return {key: source.pop(key) for key in keys if key in source}


def split(run: dict):
    """Keep task scheduling metadata small. Publish all immutable files before their DB references."""
    header = dict(run)
    header["rounds"] = []
    header["samples"] = []
    rows = []

    def add(category, key, number, commit, value):
        rows.append((run["id"], category, key, number, commit, json.dumps(value, ensure_ascii=False)))

    baseline = run["baseline"]
    add("version", "baseline", 0, baseline["commit"], baseline)
    directory = artifact_directory(run["id"])
    for source in run["rounds"]:
        record = dict(source)
        number = record["round"]
        key = f"round-{number}"
        version = record.get("source_commit")
        add("version", key, number, version, {
            "base_commit": record["base_commit"], "commit": version, "digest": record.get("digest"),
            "status": record["status"],
        })
        add("improvement", key, number, version, _take(record, ("design", "evidence_samples", "summary", "diff", "frozen_candidate")))
        add("evaluation", key, number, version, _take(record, ("startup_check", "error", "report", "regressions")))
        add("trace", key, number, version, _take(record, ("trace_ids",)))
        header["rounds"].append(record)
    for source in run["samples"]:
        sample = dict(source)
        key, number, version = sample["id"], sample["round"], sample["source_commit"]
        outputs = sample.pop("artifacts", {})
        add("artifact", key, number, version, {name: _store_text(directory, value) for name, value in outputs.items()})
        add("evaluation", key, number, version, _take(sample, ("checks", "scores", "judge_results", "judge_errors")))
        add("trace", key, number, version, _take(sample, ("trace_ids", "evaluation_trace_ids", "generation_retries")))
        header["samples"].append(sample)
    return header, rows


def save_entries(conn, rows):
    conn.executemany("""INSERT INTO assessment_entries VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(assessment_id, category, entry_id) DO UPDATE SET
        version=excluded.version, payload=excluded.payload
        WHERE version IS NOT excluded.version OR payload != excluded.payload""", rows)


def hydrate(run: dict) -> dict:
    targets = {f"round-{r['round']}": r for r in run["rounds"]}
    targets.update({s["id"]: s for s in run["samples"]})
    directory = artifact_directory(run["id"])
    for row in db.query_all("SELECT category,entry_id,payload FROM assessment_entries WHERE assessment_id=? AND category!='version'", (run["id"],)):
        record = targets[row["entry_id"]]
        value = json.loads(row["payload"])
        if row["category"] == "artifact":
            outputs = {}
            for name, digest in value.items():
                if not re.fullmatch(r"[a-f0-9]{64}", digest):
                    raise ValueError("正文产物引用无效")
                outputs[name] = (directory / digest).read_bytes().decode("utf-8")
            record["artifacts"] = outputs
        else:
            record.update(value)
    return run
