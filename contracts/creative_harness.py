"""Asset and permission contract shared by production writing and scoped evaluations."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from contracts.architecture_manifest import BASE_CHAIN_MODULES
from contracts.writing_artifacts import artifact_relpath, stage_allowed_kinds, volume_draft_relpath

AGENTS = ("outline", "volume", "chapter", "text")
LABELS = dict(zip(AGENTS, ("总纲", "卷纲", "章纲", "正文")))
ORIGIN = {"outline": "outline", "volume_outline": "volume", "chapter_outline": "chapter",
          "style": "chapter", "chapter_text": "text"}


def readable_kind(agent: str, kind: str) -> bool:
    if agent not in AGENTS:
        return False
    return kind == "character" or (
        kind in ORIGIN and AGENTS.index(ORIGIN[kind]) <= AGENTS.index(agent)
    )


def publication_path(args: dict) -> str:
    if args.get("draft"):
        if args.get("kind") != "volume_outline":
            raise ValueError("只有卷纲可保存修订草稿")
        return volume_draft_relpath(args.get("volume"))
    return artifact_relpath(args["kind"], name=args.get("name"), volume=args.get("volume"), chapter=args.get("chapter"))


def entry_missing(workspace: Path, agent: str, volume: int = 1, chapter: int = 1) -> list[str]:
    if agent not in AGENTS or min(volume, chapter) < 1:
        raise ValueError("未知创作入口或目标卷章")
    paths = {
        "outline": [], "volume": ["总纲.md"],
        "chapter": [artifact_relpath("volume_outline", volume=volume)],
        "text": [artifact_relpath("chapter_outline", chapter=chapter), "文风.md"],
    }[agent]
    if agent == "text" and chapter > 1:
        paths.append(artifact_relpath("chapter_text", chapter=chapter - 1))
    return [p for p in paths if not (workspace / p).is_file()
            or not (workspace / p).read_text(encoding="utf-8").strip()]


def write_denial(agent: str, tool: str, args: dict, volume: int, chapter: int) -> str | None:
    if agent not in AGENTS:
        return "未知创作身份"
    kind = args.get("kind")
    if tool == "task":
        helper = {"volume": "volume_qc", "text": "review"}.get(agent)
        return None if helper and args.get("subagent_type") == helper else "当前入口不能委托这个助手"
    if tool in {"read_file", "write_file", "edit_file", "ls", "glob", "grep", "execute"}:
        return "请使用当前入口已获准的产物工具"
    if tool == "read_artifact":
        return None if readable_kind(agent, kind) else "无权读取此类产物"
    if tool == "update_character_state":
        return None if agent == "text" else "仅正文可以回写人物现状"
    if tool in {"update_foreshadow_table", "rewrite_rhythm_table", "append_chapter_deviation"}:
        return "请由产物所属 Agent 修改"
    if tool != "save_artifact":
        return None
    if kind not in stage_allowed_kinds(agent):
        return "此产物不属于当前创作入口"
    try:
        publication_path(args)
    except (ValueError, TypeError) as exc:
        return str(exc)
    if kind == "character":
        if agent == "text":
            return "正文只可用 update_character_state 更新现状和变化记录"
        role = args.get("character_role")
        if agent == "outline" and (role not in {"protagonist", "antagonist"}
                                    or (args.get("name") == "主角") != (role == "protagonist")):
            return "总纲只维护主角卡和各卷反派卡"
        if agent == "volume" and (role != "supporting" or args.get("name") == "主角"):
            return "卷纲只维护本卷配角卡"
    if kind == "volume_outline" and args.get("volume") != volume:
        return "只能写当前目标卷"
    if kind in {"chapter_outline", "chapter_text"} and args.get("chapter") != chapter:
        return "只能写当前目标章"
    return None


def build_agent_package(source: Path, destination: Path, agent: str) -> None:
    """Seed a fresh checkout; each package may then evolve independently."""
    if agent not in AGENTS:
        raise ValueError(f"未知创作 Agent: {agent}")
    source, destination = Path(source), Path(destination)
    manifest = json.loads((source / "architecture.json").read_text(encoding="utf-8"))
    author = next(a for a in manifest["agents"] if a["name"] == "author")
    delegates = {"volume": ["volume_qc"], "text": ["review"]}.get(agent, [])
    main = {**author, "name": agent, "display_name": LABELS[agent], "description": LABELS[agent] + "创作",
            "prompt": f"prompts/creative_{agent}.md", "delegates": delegates,
            "tools": ["read_artifact", "list_characters", "save_artifact", "confirm_with_user", "request_review"],
            "middleware": ["stage_gate", "tool_call_recovery"]}
    if agent == "text":
        main["tools"].append("update_character_state")
        main["middleware"].insert(1, "text_completion")
    agents = [main] + [{**a, "delegated_by": agent} for a in manifest["agents"] if a["name"] in delegates]
    files = {"__init__.py", "text_completion.py", "tools/__init__.py"}
    if agent == "text":
        files.add("prompts/workspace_style.md")
    files.update(f"middleware/{name}.py" for name in (*BASE_CHAIN_MODULES, "revision_limit"))
    for spec in agents:
        files.add(spec["prompt"])
        files.update(f"tools/{name}.py" for name in spec["tools"])
        files.update(f"middleware/{name}.py" for name in spec["middleware"])
    for relative in sorted(files):
        dest = destination / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, dest)
    for mode in ("auto", "interactive"):
        shutil.copyfile(source / f"prompts/creative_guide_{mode}.md",
                        destination / f"prompts/guide_{mode}.md")
    (destination / "architecture.json").write_text(
        json.dumps({**manifest, "agents": agents}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
