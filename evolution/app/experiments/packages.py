"""Import complete, immutable JSON task packages without touching legacy data."""
from __future__ import annotations

from contracts.writing_artifacts import (
    KIND_CHAPTER, KIND_TEXT, KIND_VOLUME, artifact_relpath, parse_relpath,
    parse_volume_chapter_range,
)
from app.experiments import repo
from app.experiments.schemas import PackageCreate


def validate_package(package: PackageCreate) -> None:
    files = package.input.files
    missing = []
    unsupported = [name for name in files if name != "demand.md" and parse_relpath(name) is None]
    if unsupported:
        raise ValueError("不是受支持的业务产物路径：" + "、".join(unsupported))
    if package.target.task_type == "volume_outlines":
        if not package.input.demand_md.strip():
            missing.append("完整创作需求 demand_md")
    else:
        required = ["主题.md", "总纲.md", "文风.md"]
        required += [artifact_relpath(KIND_VOLUME, volume=number) for number in package.target.volume_ids]
        if package.target.task_type == "chapter_outlines" and package.target.chapter is not None and package.target.chapter > 1:
            required.append(artifact_relpath(KIND_CHAPTER, chapter=package.target.chapter - 1))
        if package.target.task_type == "chapter_text":
            if not package.target.volume_ids:
                missing.append("正文所属卷号 volume_ids")
            required.append(artifact_relpath(KIND_CHAPTER, chapter=package.target.chapter))
            if package.target.chapter > 1:
                required.append(artifact_relpath(KIND_TEXT, chapter=package.target.chapter - 1))
        missing.extend(name for name in required if not files.get(name, "").strip())
        if any(name.startswith("人物/") for name in files) and not files.get("人物/主角.md", "").strip():
            missing.append("人物/主角.md")
    if any(skill.get("enabled", True) and not (skill.get("content") or skill.get("files")) for skill in package.input.skills):
        missing.append("已启用技能的固定内容")
    if not package.input.confirmation_rules.strip():
        missing.append("预设确认规则")
    if missing:
        raise ValueError("资料包缺少必需输入：" + "、".join(missing))
    if package.target.task_type == "chapter_outlines":
        for volume in package.target.volume_ids:
            name = artifact_relpath(KIND_VOLUME, volume=volume)
            if parse_volume_chapter_range(files[name]) is None:
                raise ValueError(f"{name}缺少合法且唯一的本卷章号范围")
    if package.target.task_type in {"chapter_outlines", "chapter_text"} and package.target.chapter is not None:
        ranges = [parse_volume_chapter_range(files.get(artifact_relpath(KIND_VOLUME, volume=number), ""))
                  for number in package.target.volume_ids]
        if not any(span and span[0] <= package.target.chapter <= span[1] for span in ranges):
            raise ValueError("目标章节不在提供的卷纲章号范围内")


def import_package(package: PackageCreate) -> dict:
    validate_package(package)
    record = package.model_dump(mode="json")
    record.update(id=repo.identifier("package"), task_type=package.target.task_type,
                  input_revision=package.input.input_revision, created_at=repo.now())
    repo.insert("self_evolution_packages", record)
    return {key: value for key, value in record.items() if key != "input"}


def templates() -> list[dict]:
    from app.experiments.seed_data import get_builtin_packages
    records = get_builtin_packages()
    examples = []
    for kind in ("volume_outlines", "chapter_outlines", "chapter_text"):
        record = next(item for item in records if item["task_type"] == kind and item["purpose"] == "optimization")
        payload = {key: record[key] for key in ("title", "purpose", "target", "input")}
        payload["input"].pop("input_revision", None)
        examples.append(payload)
    return examples
