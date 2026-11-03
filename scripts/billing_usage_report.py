"""Read-only, content-free historical usage sampling for default billing budgets."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def report(root: Path, limit: int):
    samples = []
    indexes = sorted(root.rglob("traces/index.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for index in indexes:
        try:
            runs = json.loads(index.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for run in runs.values():
            if not isinstance(run, dict) or run.get("status") not in {"completed", "awaiting_input"}:
                continue
            metrics = run.get("metrics") or {}
            input_tokens, output_tokens = metrics.get("input_tokens"), metrics.get("output_tokens")
            if input_tokens is None or output_tokens is None or metrics.get("input_partial") or metrics.get("output_partial"):
                continue
            samples.append({"started_at": run.get("started_at", ""),
                            "input_tokens": input_tokens, "output_tokens": output_tokens})
    samples = sorted(samples, key=lambda value: value["started_at"], reverse=True)[:limit]
    totals = sorted(row["input_tokens"] + row["output_tokens"] for row in samples)
    def percentile(values, fraction):
        return values[max(0, math.ceil(len(values) * fraction) - 1)] if values else None
    return {"sample_count": len(samples), "index_count": len(indexes),
            "total_tokens_p50": percentile(totals, .5), "total_tokens_p95": percentile(totals, .95),
            "input_tokens_p95": percentile(sorted(row["input_tokens"] for row in samples), .95),
            "output_tokens_p95": percentile(sorted(row["output_tokens"] for row in samples), .95),
            "samples": samples}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    print(json.dumps(report(args.root, args.limit)))
