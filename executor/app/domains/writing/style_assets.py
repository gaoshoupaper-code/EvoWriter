"""作品文风替换：保留阶段，失败时恢复旧卡及旧标注。"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.domains.writing.annotations import ANNOTATIONS_FILE
from app.domains.writing.run_gate import get_workspace_mutation_lock
from contracts.writing_artifacts import STYLE_FILENAME


class WritingStyleError(RuntimeError):
    """可向作者直接说明的文风读取或失效任务错误。"""


def assert_style_annotation_task_current(workspace: Path, message: str, *, allow_resolved: bool = False) -> None:
    """排队和重试仍携带派发时的任务文本，执行前必须核对原标注。"""
    if "【标注修订任务】" not in message or f"｜{STYLE_FILENAME}｜" not in message:
        return
    from app.domains.writing.annotations import load_annotations

    annotations = load_annotations(workspace)
    marker = re.search(r"<!-- annotation-ids: ([A-Za-z0-9,\-]+) -->", message)
    if marker:
        ids = set(marker.group(1).split(","))
        known = {annotation.id for annotation in annotations}
        stale = bool(ids - known) or any(
            annotation.id in ids and annotation.artifact_rel == STYLE_FILENAME
            and annotation.status not in ({"pending", "resolved"} if allow_resolved else {"pending"})
            for annotation in annotations
        )
    else:
        # 已发布客户端保存的旧任务没有 id；导入后不能继续执行旧卡待办。
        stale = any(annotation.artifact_rel == STYLE_FILENAME and annotation.status == "invalid"
                    and annotation.dispatched for annotation in annotations)
        stale = stale or not any(annotation.artifact_rel == STYLE_FILENAME
                                 and annotation.status in ({"pending", "resolved"} if allow_resolved else {"pending"})
                                 and annotation.dispatched for annotation in annotations)
    if stale:
        raise WritingStyleError("文风已替换，旧文风标注任务已失效。请新开对话，并从标注面板重新派发当前待办。")


def _stage_bytes(target: Path, content: bytes) -> Path:
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".style-", delete=False) as staged:
        path = Path(staged.name)
        try:
            staged.write(content)
            staged.flush()
            os.fsync(staged.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    return path


def replace_style_card(workspace: Path, content: str) -> int:
    with get_workspace_mutation_lock(workspace):
        card = workspace / STYLE_FILENAME
        annotations = workspace / ANNOTATIONS_FILE
        old_card = card.read_bytes() if card.exists() else None
        raw_annotations = annotations.read_bytes() if annotations.exists() else None
        invalidated = 0
        new_annotations = None
        if raw_annotations is not None:
            data = json.loads(raw_annotations)
            for annotation in data.get("annotations", []):
                if annotation["artifact_rel"] == STYLE_FILENAME and annotation["status"] == "pending":
                    annotation["status"] = "invalid"
                    annotation["updated_at"] = datetime.now(UTC).isoformat()
                    invalidated += 1
            if invalidated:
                new_annotations = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")

        staged: list[Path] = []
        replaced = False
        try:
            backup = _stage_bytes(card, old_card) if old_card is not None else None
            if backup:
                staged.append(backup)
            replacement = _stage_bytes(card, content.encode("utf-8"))
            staged.append(replacement)
            annotation_replacement = None
            if new_annotations is not None:
                annotation_replacement = _stage_bytes(annotations, new_annotations)
                staged.append(annotation_replacement)
            os.replace(replacement, card)
            replaced = True
            if annotation_replacement:
                os.replace(annotation_replacement, annotations)
        except BaseException:
            # 标注提交失败时，旧标注尚未被替换；只需恢复已经提交的卡。
            if replaced:
                if backup:
                    os.replace(backup, card)
                else:
                    card.unlink(missing_ok=True)
            raise
        finally:
            for path in staged:
                path.unlink(missing_ok=True)
        return invalidated
