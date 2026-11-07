"""Versioned inputs and durable task identities shared by the two services."""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, model_serializer

from contracts.platform.entities import LlmConfigSnapshot

TaskType = Literal["volume_outlines", "chapter_outlines", "chapter_text"]
TaskStatus = Literal[
    "pending", "running", "recovering", "completed", "failed",
    "interrupted", "stopping", "stopped",
]


class TargetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: TaskType
    volume_ids: list[int] = Field(default_factory=list)
    chapter: int | None = Field(default=None, ge=1)
    chapters_per_volume: int | None = Field(default=None, ge=1)

    @model_serializer(mode="wrap")
    def serialize_scope(self, handler):
        value = handler(self)
        # Legacy public request fingerprints must not gain a new null field.
        if self.chapters_per_volume is None:
            value.pop("chapters_per_volume", None)
        return value

    @model_validator(mode="after")
    def validate_scope(self) -> "TargetSpec":
        if any(v < 1 for v in self.volume_ids):
            raise ValueError("目标卷号必须是正整数")
        if len(set(self.volume_ids)) != len(self.volume_ids):
            raise ValueError("目标卷号不能重复")
        if self.volume_ids != sorted(self.volume_ids):
            raise ValueError("目标卷号必须按升序排列")
        if self.task_type == "volume_outlines":
            if not self.volume_ids or any(number != index for index, number in enumerate(self.volume_ids, 1)):
                raise ValueError("整本卷纲必须声明从第1卷开始的完整目标范围")
        elif self.task_type == "chapter_outlines":
            if self.chapter is not None and len(self.volume_ids) != 1:
                raise ValueError("单章章纲必须指定一个所属卷和目标章号")
            if self.chapter is None and len(self.volume_ids) != 4:
                raise ValueError("章纲任务必须指定四个目标卷")
        elif self.chapter is None:
            raise ValueError("正文任务必须指定目标章号")
        if self.task_type == "volume_outlines" and self.chapter is not None:
            raise ValueError("整本卷纲不接受单章目标")
        if self.chapters_per_volume is not None and self.task_type != "volume_outlines":
            raise ValueError("仅整本卷纲可以固定每卷章数")
        return self


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    demand_md: str = ""
    files: dict[str, str] = Field(default_factory=dict)
    skills: list[dict[str, Any]] = Field(default_factory=list)
    confirmation_rules: str = Field(min_length=1)
    input_revision: str = ""

    @field_validator("skills")
    @classmethod
    def validate_skills(cls, skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for skill in skills:
            if any(not isinstance(skill.get(key), str) or not skill[key].strip()
                   for key in ("stage", "name", "content")):
                raise ValueError("技能快照必须提供非空的 stage、name 和 content")
        return skills

    @field_validator("files")
    @classmethod
    def validate_files(cls, files: dict[str, str]) -> dict[str, str]:
        for name in files:
            path = PurePosixPath(name)
            if (
                not name or "\\" in name or ":" in name or "\x00" in name
                or path.is_absolute() or str(path) != name
                or any(part.startswith(".") for part in path.parts)
                or path.suffix.lower() != ".md"
            ):
                raise ValueError(f"资料包只能包含规范的相对 Markdown 产物路径：{name}")
        return files

    @model_validator(mode="after")
    def bind_revision(self) -> "TaskInput":
        payload = self.model_dump(exclude={"input_revision"}, mode="json")
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        revision = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if self.input_revision and self.input_revision != revision:
            raise ValueError("输入版本与实际资料内容不一致")
        self.input_revision = revision
        return self


class TargetTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_-]+$")
    attempt_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_-]+$")
    source_commit: str = Field(pattern=r"^[0-9a-f]{7,64}$")
    artifact_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model_snapshot: LlmConfigSnapshot | None = None
    target: TargetSpec
    input: TaskInput
    traceparent: str | None = None


class TargetTaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: TaskStatus
    phase: str = ""
    completed: bool = False
    trace_ids: list[str] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    checks: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    input_revision: str = ""
    source_commit: str = ""
    artifact_digest: str | None = None
    attempts: int = 0
    created_at: str = ""
    updated_at: str = ""
