"""Apply role-scoped manual Prompt and Skill edits to an isolated checkout."""
from __future__ import annotations

import ast
import json
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.versioning.elements_api import _extract_stage_prompt_files, _prompt_title
from contracts.architecture_manifest import load_architecture_manifest


class DraftRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_version: int = Field(ge=1)
    revision: int = Field(ge=0)


class DraftEdit(DraftRevision):
    role: str
    operation: Literal["prompt", "skill_edit", "skill_import", "skill_delete"]
    path: str | None = None
    filename: str | None = None
    content: str | None = None


def _path(root: Path, value: str) -> Path:
    relative = PurePosixPath(value)
    if (not value or relative.is_absolute() or relative.as_posix() != value or "\\" in value
            or ":" in value or any(part.startswith(".") for part in relative.parts)):
        raise ValueError("资产路径无效。")
    target = root.joinpath(*relative.parts)
    if target.resolve() != target:
        raise ValueError("资产路径不能使用链接。")
    return target


def _skill_header(content: str) -> tuple[dict, str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content
    for index, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            header = yaml.safe_load("\n".join(lines[1:index]))
            if not isinstance(header, dict):
                raise ValueError("SKILL.md 文件头格式无效。")
            return header, "\n".join(lines[index + 1:])
    raise ValueError("SKILL.md 文件头缺少结束分隔线。")


def _skill_description(content: str) -> str | None:
    try:
        value = _skill_header(content)[0].get("description")
    except (ValueError, yaml.YAMLError):
        return None
    return value if isinstance(value, str) else None


def _read_contents(target: Path) -> str:
    return target.read_text(encoding="utf-8")


def _stage_paths(root: Path, actors: list[dict], actor: dict) -> list[str]:
    main = next((row for row in actors if row["role"] == "main"), None)
    if actor is not main:
        return []
    entry = _path(root, "__init__.py")
    if not entry.is_file():
        return []
    return [f"prompts/{name}" for name in _extract_stage_prompt_files(_read_contents(entry))]


def _prompt_paths(root: Path, actors: list[dict], actor: dict) -> list[tuple[str, str]]:
    return [(actor["prompt"], "base"), *((path, "stage") for path in _stage_paths(root, actors, actor)
                                      if path != actor["prompt"] and _path(root, path).is_file())]


def _replace_stage_reference(root: Path, original: str, replacement: str) -> None:
    """Change only the trusted package declaration that points to a shared prompt."""
    path = _path(root, "__init__.py")
    source = path.read_bytes()
    tree = ast.parse(source.decode("utf-8"))
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    spans = []
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        if not any(isinstance(target, ast.Name) and target.id == "STAGE_PROMPT_FILES" for target in targets):
            continue
        if isinstance(node.value, ast.Dict):
            for value in node.value.values:
                if isinstance(value, ast.Constant) and value.value == original.removeprefix("prompts/"):
                    start = offsets[value.lineno - 1] + value.col_offset
                    end = offsets[value.end_lineno - 1] + value.end_col_offset
                    spans.append((start, end))
    if not spans:
        raise ValueError("无法调整共用提示词的段规程引用。")
    literal = repr(replacement.removeprefix("prompts/")).encode("utf-8")
    for start, end in sorted(spans, reverse=True):
        source = source[:start] + literal + source[end:]
    path.write_bytes(source)


def _open_role(root: Path, role: str) -> tuple[dict, dict]:
    path = _path(root, "architecture.json")
    validated = load_architecture_manifest(root)
    if not validated.agent(role):
        raise ValueError("所选角色不存在于本套 Harness。")
    manifest = json.loads(_read_contents(path))
    actor = next(row for row in manifest["agents"] if row["name"] == role)
    return manifest, actor


def _write_manifest(root: Path, manifest: dict) -> None:
    data = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    _path(root, "architecture.json").write_bytes(data.encode("utf-8"))


def _move_prompt_references(root: Path, manifest: dict, actor: dict, old: str, new: str) -> None:
    if old in _stage_paths(root, manifest["agents"], actor):
        _replace_stage_reference(root, old, new)
    if actor["prompt"] == old:
        actor["prompt"] = new
        _write_manifest(root, manifest)


def _editable_content(edit: DraftEdit) -> str:
    if edit.content is None or not edit.content.strip() or "\x00" in edit.content:
        raise ValueError("请填写有效的 UTF-8 文本。")
    return edit.content


def _free_prompt(root: Path, role: str, before: Path) -> Path:
    stem = f"{role}_{before.stem}"
    i = 1
    while True:
        suffix = f"_{i}" if i > 1 else ""
        target = root / "prompts" / f"{stem}{suffix}{before.suffix}"
        if not target.exists() and not target.is_symlink():
            return target
        i += 1


def _edit_prompt(root: Path, manifest: dict, actor: dict, edit: DraftEdit) -> dict:
    roles_by_path = {
        row["name"]: dict(_prompt_paths(root, manifest["agents"], row))
        for row in manifest["agents"]
    }
    path = edit.path
    if path not in roles_by_path[actor["name"]] or not path.startswith("prompts/") or not path.endswith(".md"):
        raise ValueError("这份提示词不属于当前角色。")
    target = _path(root, path)
    if not target.is_file():
        raise ValueError("当前版本缺少这份提示词。")
    old = _read_contents(target)
    content = _editable_content(edit)
    name = _prompt_title(old, path)
    if old != content:
        if any(path in paths for role, paths in roles_by_path.items() if role != actor["name"]):
            target = _free_prompt(root, actor["name"], target)
            _move_prompt_references(root, manifest, actor, path, target.relative_to(root).as_posix())
        target.write_bytes(content.encode("utf-8"))
    return _change(edit, name, target.relative_to(root).as_posix(), old, content)


def _skill_name(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[^\W_][\w.-]{0,79}", name) or name.endswith("."):
        raise ValueError("技能名须以字母或数字开头，可包含汉字、点、下划线和短横线。")
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(
            f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10))}:
        raise ValueError("该技能名不可用。")
    return name


def _valid_skill(content: str, *, importing: bool = False) -> dict:
    try:
        metadata, body = _skill_header(content)
    except yaml.YAMLError as exc:
        raise ValueError("SKILL.md 文件头的 YAML 格式无效。") from exc
    if importing or "name" in metadata:
        if not metadata.get("name"):
            raise ValueError("SKILL.md 文件头缺少技能名 name。")
        _skill_name(metadata["name"])
    if importing:
        description = metadata.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("SKILL.md 文件头缺少用途 description。")
    if not body.strip():
        raise ValueError("SKILL.md 缺少正文。")
    return metadata


def _skill_aliases(root: Path, actor: dict, exclude: str | None = None) -> set[str]:
    aliases = set()
    for namespace in actor.get("skills", []):
        if namespace == exclude:
            continue
        directory = _path(root, f"skills/{namespace}")
        aliases.add(directory.name.casefold())
        try:
            entry = _path(root, f"skills/{namespace}/SKILL.md")
            name = _skill_header(_read_contents(entry))[0].get("name")
        except (OSError, ValueError, yaml.YAMLError):
            continue
        if isinstance(name, str):
            aliases.add(name.casefold())
    return aliases


def _free_skill(root: Path, manifest: dict, role: str, name: str) -> Path:
    root_directory = _path(root, "skills")
    installed = {directory.casefold() for row in manifest["agents"] for directory in row.get("skills", [])}
    i = 0
    while True:
        prefix = f"manual-{role}{'-' + str(i) if i > 1 else ''}"
        relative = name if i == 0 else f"{prefix}/{name}"
        # Match case-insensitively so imports work in Windows checkouts as well.
        target = root_directory.joinpath(*relative.split("/"))
        if not any(relative.casefold() == path or relative.casefold().startswith(path + "/")
                   or path.startswith(relative.casefold() + "/") for path in installed):
            if not target.exists() and not target.is_symlink() and (
                    not target.parent.exists() or target.parent.is_dir()):
                return _path(root, f"skills/{relative}")
        i += 1


def _skill_children(directory: Path):
    for child in directory.iterdir():
        if child.is_symlink():
            raise ValueError("技能文件不能使用链接。")
        if child.is_dir() and (child / "SKILL.md").is_file():
            continue
        yield child


def _copy_skill(directory: Path, target: Path) -> None:
    target.mkdir(parents=True)
    for child in _skill_children(directory):
        if child.is_dir():
            _copy_skill(child, target / child.name)
        else:
            shutil.copy2(child, target / child.name)


def _delete_skill(directory: Path) -> None:
    for child in _skill_children(directory):
        if child.is_dir():
            _delete_skill(child)
        else:
            child.unlink()
    if not any(directory.iterdir()):
        directory.rmdir()


def _change(edit: DraftEdit, name: str, path: str, before: str | None, after: str | None) -> dict:
    return {"role": edit.role, "operation": edit.operation, "name": name,
            "path": path, "origin": edit.path or path, "before": before, "after": after}


def restore_copy(root: Path, change: dict) -> None:
    """Drop a private copy on undo only if reusing its original would preserve the user's text."""
    if change["path"] == change["origin"] or change["after"] is None:
        return
    copy = _path(root, change["path"])
    origin = _path(root, change["origin"])
    manifest, actor = _open_role(root, change["role"])
    if change["operation"] == "prompt":
        if origin.is_file() and _read_contents(origin) == change["after"]:
            _move_prompt_references(root, manifest, actor, change["path"], change["origin"])
            copy.unlink()
    else:
        entry = _path(root, f"{change['origin']}/SKILL.md")
        previous = change["origin"].removeprefix("skills/")
        current = change["path"].removeprefix("skills/")
        if entry.is_file() and _read_contents(entry) == change["after"] and previous not in actor["skills"]:
            actor["skills"][actor["skills"].index(current)] = previous
            _write_manifest(root, manifest)
            _delete_skill(copy)


def apply_edit(root: Path, edit: DraftEdit) -> dict:
    manifest, actor = _open_role(root, edit.role)
    if edit.operation == "prompt":
        return _edit_prompt(root, manifest, actor, edit)
    selected = actor.setdefault("skills", [])
    if edit.operation == "skill_import":
        if edit.filename != "SKILL.md":
            raise ValueError("只能导入名为 SKILL.md 的单个文件。")
        content = _editable_content(edit)
        name = _valid_skill(content, importing=True)["name"]
        if name.casefold() in _skill_aliases(root, actor):
            raise ValueError(f"当前角色已有同名技能「{name}」。")
        target = _free_skill(root, manifest, edit.role, name)
        target.mkdir(parents=True)
        (target / "SKILL.md").write_bytes(content.encode("utf-8"))
        selected.append(target.relative_to(root / "skills").as_posix())
        _write_manifest(root, manifest)
        return _change(edit, name, target.relative_to(root).as_posix(), None, content)

    if not edit.path or not edit.path.startswith("skills/") or edit.path.removeprefix("skills/") not in selected:
        raise ValueError("这个技能不属于当前角色。")
    namespace = edit.path.removeprefix("skills/")
    directory = _path(root, edit.path)
    source = _path(root, f"{edit.path}/SKILL.md")
    old = _read_contents(source)
    others = [row for row in manifest["agents"] if row is not actor]
    shared = any(namespace in row.get("skills", []) for row in others)
    if edit.operation == "skill_delete":
        selected.remove(namespace)
        if not shared:
            _delete_skill(directory)
        _write_manifest(root, manifest)
        return _change(edit, directory.name, edit.path, old, None)
    content = _editable_content(edit)
    metadata = _valid_skill(content)
    alias = metadata.get("name")
    if alias and alias.casefold() in _skill_aliases(root, actor, exclude=namespace):
        raise ValueError(f"当前角色已有同名技能「{alias}」。")
    target = directory
    if old != content:
        if shared:
            target = _free_skill(root, manifest, edit.role, directory.name)
            _copy_skill(directory, target)
            selected[selected.index(namespace)] = target.relative_to(root / "skills").as_posix()
            _write_manifest(root, manifest)
        (target / "SKILL.md").write_bytes(content.encode("utf-8"))
    return _change(edit, target.name, target.relative_to(root).as_posix(), old, content)


def draft_agents(root: Path) -> list[dict]:
    manifest = json.loads(_read_contents(root / "architecture.json"))
    agents = []
    for actor in manifest["agents"]:
        prompts = []
        for relative, kind in _prompt_paths(root, manifest["agents"], actor):
            path = _path(root, relative)
            if path.is_file():
                body = _read_contents(path)
                prompts.append({"path": relative, "kind": kind, "body": body,
                                "title": _prompt_title(body, relative)})
        skills = []
        for namespace in actor.get("skills", []):
            relative = f"skills/{namespace}"
            directory = _path(root, relative)
            entry = _path(root, f"{relative}/SKILL.md")
            try:
                content = _read_contents(entry)
                load_error = None
            except (ValueError, OSError) as exc:
                content, load_error = None, str(exc)
            skills.append({"path": relative, "name": directory.name, "content": content,
                           "description": _skill_description(content) if content else None,
                           "load_error": load_error})
        agents.append({"name": actor["name"], "prompt_files": prompts, "skills": skills})
    return agents
