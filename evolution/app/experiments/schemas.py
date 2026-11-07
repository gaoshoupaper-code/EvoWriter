"""Public experiment inputs; immutable records are created from validated values."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from contracts.target_tasks import TargetSpec, TaskInput, TaskType

TASK_TYPES = ("volume_outlines", "chapter_outlines", "chapter_text")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PackageCreate(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    purpose: Literal["optimization", "validation", "regression"]
    target: TargetSpec
    input: TaskInput


class Dimension(StrictModel):
    key: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1)
    anchors: dict[str, str]
    weight: float = Field(default=1, gt=0, allow_inf_nan=False)

    @field_validator("anchors")
    @classmethod
    def validate_anchors(cls, value):
        if set(value) != {"1", "2", "3", "4", "5"} or any(not text.strip() for text in value.values()):
            raise ValueError("必须提供1至5分的完整非空档位")
        return value


class RuleCreate(StrictModel):
    task_type: TaskType
    name: str = Field(min_length=1, max_length=200)
    profile: Literal["stage-eval-v2"] | None = None
    dimensions: list[Dimension] = Field(min_length=1)
    discipline_rules: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_keys(self):
        keys = [dimension.key for dimension in self.dimensions]
        if len(set(keys)) != len(keys):
            raise ValueError("评分维度不能重复")
        if any(not text.strip() for text in self.discipline_rules):
            raise ValueError("评分纪律不能为空")
        return self


class Thresholds(StrictModel):
    completion_rate: float = Field(default=1, ge=0, le=1, allow_inf_nan=False)
    quality_gain: float = Field(default=.1, ge=0, allow_inf_nan=False)
    regression_tolerance: dict[TaskType, float] = Field(default_factory=dict)

    @field_validator("regression_tolerance")
    @classmethod
    def validate_tolerances(cls, value):
        import math
        if any(not math.isfinite(number) or number < 0 for number in value.values()):
            raise ValueError("退化容忍度必须是非负有限数")
        return value


class RunCreate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    task_type: TaskType
    baseline_version: int = Field(ge=1)
    model_config_id: int = Field(ge=1)
    judge_group_id: int = Field(ge=1)
    rule_versions: dict[TaskType, str]
    optimization_ids: list[str] = Field(min_length=1)
    validation_ids: list[str] = Field(min_length=1)
    regression_ids: dict[TaskType, list[str]]
    thresholds: Thresholds = Field(default_factory=Thresholds)
    max_rounds: int = Field(default=3, ge=1)

    @model_validator(mode="after")
    def validate_sets(self):
        other_types = set(TASK_TYPES) - {self.task_type}
        if not self.thresholds.regression_tolerance:
            self.thresholds.regression_tolerance = {kind: 0 for kind in other_types}
        if set(self.rule_versions) != set(TASK_TYPES):
            raise ValueError("必须指定三类任务各自的评分规则版本")
        if set(self.regression_ids) != other_types or any(not ids for ids in self.regression_ids.values()):
            raise ValueError("必须指定另外两类各自的回归样本")
        if set(self.thresholds.regression_tolerance) != other_types:
            raise ValueError("必须指定另外两类各自的退化容忍度")
        all_ids = self.optimization_ids + self.validation_ids + [identifier for ids in self.regression_ids.values() for identifier in ids]
        if len(set(all_ids)) != len(all_ids):
            raise ValueError("优化、终验和回归样本不能重复或交叉")
        return self
