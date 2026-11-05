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
    elif package.target.task_type == "chapter_text":
        ranges = [parse_volume_chapter_range(text) for name, text in files.items() if name.startswith("卷纲/")]
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
    common = {"confirmation_rules": "依照主题和总纲自行决定普通创作取舍；不跳过生成与审稿检查。", "skills": []}
    records = [
        {"title": "整本两卷卷纲示例", "purpose": "optimization", "target": {"task_type": "volume_outlines", "volume_ids": [1, 2]},
         "input": {**common, "demand_md": "请创作一部两卷小说。请在此填写题材、核心设定、主线和完整创作要求。", "files": {}}},
        {"title": "指定四卷章纲示例", "purpose": "optimization", "target": {"task_type": "chapter_outlines", "volume_ids": [1, 2, 3, 4]},
         "input": {**common, "demand_md": "完成第1至4卷的全部章纲。", "files": {"主题.md": "信使在洪水前完成四段接力。", "总纲.md": "第一卷出发，第二卷渡河，第三卷寻找失踪同伴，第四卷送达预警。", "文风.md": "采用具体行动和简洁对话，保持紧张感。", **{artifact_relpath(KIND_VOLUME, volume=i): f"> 状态：定稿\n本卷章号范围：第{(i - 1) * 5 + 1}-{i * 5}章\n信使完成本段接力，遭遇阻力并作出有代价的选择。" for i in range(1, 5)}}}},
        {"title": "单章正文示例", "purpose": "validation", "target": {"task_type": "chapter_text", "volume_ids": [1], "chapter": 1},
         "input": {**common, "demand_md": "完成第1章正文及审稿修订。", "files": {"主题.md": "信使在洪水前送出预警。", "总纲.md": "主角受托送信，克服洪水阻隔，在下游疏散前抵达。", artifact_relpath(KIND_VOLUME, volume=1): "> 状态：定稿\n本卷章号范围：第1-5章\n主角从上游出发，寻找安全渡河路线。", artifact_relpath(KIND_CHAPTER, chapter=1): "> 状态：定稿\n主角接到预警信，发现吊桥损毁，决定带信沿旧堤出发。", "文风.md": "使用具体行动和简洁对话，避免空泛形容。"}}},
    ]
    return records
