"""Capture readable run identity from owned metadata and explicit task scope."""
from __future__ import annotations

import logging

from contracts.trace.payload import sanitize_structural_text

logger = logging.getLogger("writer.trace_display")


def capture_display_context(thread, *, task_phase=None, task_type=None, execution=None, owner_id=None):
    context = {}
    if task_phase in {"theme", "outline", "volume", "chapter", "text", "legacy"}:
        context["task_phase"] = task_phase
    try:
        from app.platform.core.db import WorkspaceRepository, get_database

        workspace = WorkspaceRepository(get_database()).get(
            thread.workspace_id, owner_id or thread.user_id,
        )
        if workspace and workspace.get("title"):
            context["workspace_title"] = sanitize_structural_text(workspace["title"])
    except Exception as exc:
        # Display metadata is optional; a missing cache/database cannot stop writing.
        logger.debug("Workspace display metadata unavailable: %s", type(exc).__name__)
    if execution is not None:
        try:
            target = execution.request.target.model_dump(exclude_none=True)
            context["target"] = {key: target[key] for key in (
                "task_type", "volume_ids", "chapter", "chapters_per_volume",
            ) if key in target}
            context["task_type"] = execution.request.target.task_type
        except Exception as exc:
            logger.debug("Target display metadata unavailable: %s", type(exc).__name__)
    if task_type:
        context["task_type"] = task_type
    return context
