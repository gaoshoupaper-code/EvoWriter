"""elements_api —— Harness 要素展示端点（数据源：git 源文件 + Platform 账本）。

从 harness 独立仓库的 git commit 读取真实源文件，投影成面向展示的结构化视图，
供前端「Harness 要素」页渲染（Prompt/Skills/Tools/Middleware/Subagents 五要素）。

version→commit 解析走 Platform 账本（platform_ledger，REQ-20260923-145931）——
registry.json 已冻结退役，账本是版本号的唯一活跃数据源。

端点（/api/snapshots 前缀）：
  GET /snapshots/{version}/harness-elements          Harness 要素展示视图（含 agents + tools）
  GET /snapshots/{version}/harness-elements/memory   记忆子系统要素视图（NWM 6 要素）
  GET /snapshots/{version}/source                    指定文件源码（middleware 懒加载用）

性能（2026-07-18）：build_elements_view / build_memory_elements_view 每次会对每个
skill/middleware/tool 文件 fork 一个 git show 子进程，一次请求 20-40 个子进程，
容器内 2-5 秒。因 harness 版本（git commit）不可变，按 version 进程内缓存视图，
TTL 60s 兜底。版本切换热路径从 N 个 git show 降到 0。
"""
from __future__ import annotations

import ast
import json
import logging
import time
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Query

from contracts.architecture_manifest import (
    ARCHITECTURE_MANIFEST_FILENAME,
    BASE_CHAIN_MODULES,
    ArchitectureManifest,
)

from app.core.git_ops import show_file
from app.versioning import platform_ledger
from app.versioning.constants import MEMORY_FILES, MEMORY_ROLE_ORDER, TOOL_SCOPE_MAP
from app.versioning.middleware_projection import build_middleware_projection

logger = logging.getLogger("evolution.elements_api")

# ── 视图缓存（2026-07-18）─────────────────────────────────────
# harness 版本 = git commit，commit 内容不可变 → 同 version 的视图永远一致，
# 可安全长期缓存。TTL 仅作防御性兜底（防万一有绕过 version 的异常写入）。
_CACHE_TTL = 60.0  # 秒
_elements_cache: dict[int, tuple[float, dict[str, Any]]] = {}
_memory_cache: dict[int, tuple[float, dict[str, Any]]] = {}


def _cached_build(
    version: int,
    cache: dict[int, tuple[float, dict[str, Any]]],
    builder: "Any",
) -> dict[str, Any]:
    """按 version 命中缓存，过期/缺失则调 builder 构建并写入。

    builder 是无参闭包（捕获 version），返回视图 dict。
    """
    hit = cache.get(version)
    if hit and (time.monotonic() - hit[0]) < _CACHE_TTL:
        return hit[1]
    view = builder()
    cache[version] = (time.monotonic(), view)
    return view

router = APIRouter(prefix="/snapshots", tags=["snapshots"])

# ── Agent 结构：按版本布局探测（v7 静态映射 / v14 目录推导）──
#
# v14 多 Agent 架构（REQ-20260922-162823）：prompts/v14/orchestrator_system.md
# 存在即判定 v14 布局，领域 agent 从 prompts/v14/domain_*.md 推导（不写死清单）；
# 领域 agent 的 system prompt = common_rules.md + 分隔线 + 领域文件（镜像
# subagents/orchestrator.py 的运行时拼接）。旧版本回退 v7 两泳道静态映射。

# (name, kind, prompt_files)——prompt_files 为相对包根路径，多文件按序拼接
_AGENT_SPECS_V7: list[tuple[str, str, tuple[str, ...]]] = [
    ("storybuilding", "story_expert", ("prompts/storybuilding_system.md",)),
    ("storybuilding_review", "reviewer", ("prompts/storybuilding_review.md",)),
]

_V14_PROMPTS_DIR = "prompts/v14"
_V14_ORCHESTRATOR_PROMPT = f"{_V14_PROMPTS_DIR}/orchestrator_system.md"
# 领域 prompt 拼接分隔线，与 orchestrator.py 运行时拼接保持一致
_PROMPT_JOINER = "\n\n---\n\n"

_SUBAGENT_ROLE_MAP: dict[str, str] = {
    "storybuilding": "故事专家（剧情大纲设计）",
    "storybuilding_review": "故事审查",
    "orchestrator": "主控编排（v14 多 Agent 架构）",
    "worldview": "世界观构建",
    "character": "人物构建",
    "storyline": "故事线构建",
}


def _manifest_at_commit(commit: str | None) -> ArchitectureManifest | None:
    """读某 commit 的架构清单（无清单返回 None → 调用方回退静态布局探测）。

    REQ-20261006-130414 FR-001/006：清单版本的要素视图直接以清单为真相源，
    源码解析投影退役；schema 解析失败按无清单回退（旧探测仍可用）。
    """
    if not commit:
        return None
    try:
        raw = show_file(commit, ARCHITECTURE_MANIFEST_FILENAME)
        return ArchitectureManifest.model_validate(json.loads(raw))
    except Exception:  # noqa: BLE001 — 无清单 / 解析失败统一回退旧探测
        logger.debug("架构清单缺失或解析失败 @ %s", commit, exc_info=True)
        return None


def _manifest_middleware_stacks(
    commit: str | None, manifest: ArchitectureManifest
) -> dict[str, list[dict[str, Any]]]:
    """清单版本的中间件栈投影：git 读 middleware 源码 → 共享清单投影函数。"""
    sources: dict[str, str] = {}
    if commit:
        for path in _list_files_at_commit(commit, "middleware"):
            if path.endswith(".py") and not path.endswith("__init__.py"):
                try:
                    sources[path] = show_file(commit, path)
                except Exception:  # noqa: BLE001
                    pass
    from app.versioning.middleware_projection import project_manifest_stacks

    return project_manifest_stacks(sources, manifest)


def _commit_unmounted(
    commit: str | None, manifest: ArchitectureManifest
) -> dict[str, list[str]]:
    """清单外孤儿文件报告（git commit 级，DEC-005 桌面「未挂载」展示）。"""
    if not commit:
        return {"subagents": [], "middleware": [], "prompts": [], "skills": []}

    def _files(subdir: str) -> set[str]:
        return set(_list_files_at_commit(commit, subdir))

    referenced_mw = {mw for a in manifest.agents for mw in a.middleware}
    referenced_skills = {s for a in manifest.agents for s in a.skills}
    referenced_prompts = {a.prompt for a in manifest.agents}
    referenced_defs = {a.definition for a in manifest.agents if a.definition}

    all_mw = _files("middleware")
    all_skills = _files("skills")
    all_prompts = _files("prompts")
    all_subs = _files("subagents")

    unmounted_mw = sorted(
        p[len("middleware/"):-len(".py")] for p in all_mw
        if p.endswith(".py") and not p.endswith("__init__.py")
        and p[len("middleware/"):-len(".py")] not in referenced_mw
        and p[len("middleware/"):-len(".py")] not in BASE_CHAIN_MODULES
        and p[len("middleware/"):-len(".py")] != "revision_limit"
    )
    unmounted_skills = sorted(
        {p.split("/")[1] for p in all_skills if p.startswith("skills/") and len(p.split("/")) > 2}
        - referenced_skills
    )
    unmounted_prompts = sorted(p for p in all_prompts if p not in referenced_prompts)
    unmounted_subs = sorted(
        p for p in all_subs
        if p.endswith(".py") and not p.endswith("__init__.py") and p not in referenced_defs
    )
    return {
        "subagents": unmounted_subs,
        "middleware": unmounted_mw,
        "prompts": unmounted_prompts,
        "skills": unmounted_skills,
    }


def _agent_specs_for_commit(
    commit: str | None,
) -> tuple[list[tuple[str, str, tuple[str, ...]]], str]:
    """探测某 commit 的 Agent 结构布局。

    Returns:
        (specs, layout)：specs = [(name, kind, prompt_files)]，
        layout ∈ v7 | v14。
    """
    if commit and _file_exists_at_commit(commit, _V14_ORCHESTRATOR_PROMPT):
        specs: list[tuple[str, str, tuple[str, ...]]] = [
            ("orchestrator", "orchestrator", (_V14_ORCHESTRATOR_PROMPT,)),
        ]
        # 领域 agent 从 domain_*.md 推导（排序保证稳定展示顺序）
        domain_files = sorted(
            f for f in _list_files_at_commit(commit, _V14_PROMPTS_DIR)
            if f.startswith(f"{_V14_PROMPTS_DIR}/domain_") and f.endswith(".md")
        )
        for path in domain_files:
            # prompts/v14/domain_worldview.md → worldview
            name = path.rsplit("/", 1)[-1].removesuffix(".md").removeprefix("domain_")
            specs.append((name, "domain", (f"{_V14_PROMPTS_DIR}/common_rules.md", path)))
        specs.append(("storybuilding_review", "reviewer", ("prompts/storybuilding_review.md",)))
        return specs, "v14"
    return _AGENT_SPECS_V7, "v7"


def _agent_skills(
    skills: list[dict[str, Any]],
    agent_name: str,
    kind: str,
    layout: str,
) -> list[dict[str, Any]]:
    """某 agent 的 skill 列表（按布局归属）。

    v7：skills/{agent_name}/ 前缀归属（reviewer 无技能）。
    v14：4 个技能经 compose_skills_backend 全部挂在 orchestrator 顶层
    （SKILL_DIRS），领域 agent 与 reviewer 无直接技能。
    """
    if kind == "reviewer":
        return []
    if layout == "v14":
        if agent_name != "orchestrator":
            return []
        return [s for s in skills if s["path"].startswith("skills/v14/")]
    return [s for s in skills if s["path"].startswith(f"skills/{agent_name}")]


# ── git 源文件读取辅助 ─────────────────────────────────────────


def _version_to_commit(version: int) -> str | None:
    """Resolve a version to its immutable commit via the Platform ledger."""
    return platform_ledger.resolve_commit(version)


def _require_ledger_version(version: int) -> None:
    """404 校验：版本必须存在于 Platform 账本；账本不可达转 502。"""
    try:
        entry = platform_ledger.get_version(version)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=404, detail=f"版本 v{version} 不存在")


def _list_files_at_commit(commit: str, subdir: str) -> list[str]:
    """列某 commit 下指定子目录的所有文件路径（相对仓库根）。

    Args:
        commit: git commit hash
        subdir: 子目录（如 "prompts"、"skills"、"middleware"）
    """
    try:
        from app.core.git_ops import _git, read_dir
        out = _git(["ls-tree", "-r", "--name-only", commit, subdir], read_dir())
        return [f for f in out.splitlines() if f.strip()] if out.strip() else []
    except Exception:  # noqa: BLE001
        logger.debug("ls-tree 失败: %s @ %s", subdir, commit, exc_info=True)
        return []


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """从 markdown 全文提 YAML front matter（首尾 --- 之间）。无则返回 {}。"""
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    return yaml.safe_load(parts[1]) or {}


def _read_prompt(commit: str | None, prompt_path: str) -> str:
    """读单条 prompt 文件全文（相对包根路径）。commit=None 或读取失败返回空串。"""
    if not commit:
        return ""
    try:
        return show_file(commit, prompt_path)
    except Exception:  # noqa: BLE001
        logger.debug("prompt 读取失败: %s @ %s", prompt_path, commit)
        return ""


def read_prompt_body(commit: str | None, prompt_files: tuple[str, ...]) -> str:
    """按 spec 拼 agent 的 system prompt 全文（多文件 = 领域拼接，镜像运行时）。"""
    parts = [_read_prompt(commit, path) for path in prompt_files]
    return _PROMPT_JOINER.join(part for part in parts if part)


def _build_skill_infos(commit: str | None) -> list[dict[str, Any]]:
    """扫 skills/ 目录，读每个 SKILL.md 的全文 + frontmatter description。"""
    if not commit:
        return []
    skill_files = _list_files_at_commit(commit, "skills")
    skills: list[dict[str, Any]] = []
    seen_dirs: set[str] = set()
    for f in skill_files:
        if not f.endswith("/SKILL.md"):
            continue
        # skill 路径 = SKILL.md 的父目录（如 skills/meta/auto-pipeline）
        skill_path = f.rsplit("/SKILL.md", 1)[0]
        if skill_path in seen_dirs:
            continue
        seen_dirs.add(skill_path)
        name = skill_path.split("/")[-1]
        try:
            content = show_file(commit, f)
            description = _parse_frontmatter(content).get("description")
            skills.append({"path": skill_path, "name": name,
                           "description": description, "content": content, "load_error": None})
        except Exception as e:  # noqa: BLE001
            skills.append({"path": skill_path, "name": name,
                           "description": None, "content": None, "load_error": str(e)})
    return skills


_ASSEMBLY_SOURCE_PATHS = (
    "__init__.py",
    "subagents/storybuilding.py",
    "subagents/factory.py",
    "subagents/reviewers/storybuilding.py",
    # v14 多 Agent 架构装配源（旧版本无此文件，缺失即不进投影）
    "subagents/orchestrator.py",
)


def _build_middleware_stacks(commit: str | None) -> dict[str, list[dict[str, Any]]]:
    """Read versioned assembly sources and project the mounted middleware stacks."""
    if not commit:
        return {}
    paths = set(_ASSEMBLY_SOURCE_PATHS)
    paths.update(
        path
        for path in _list_files_at_commit(commit, "middleware")
        if path.endswith(".py")
    )
    sources: dict[str, str] = {}
    for path in paths:
        try:
            sources[path] = show_file(commit, path)
        except Exception:  # noqa: BLE001
            logger.debug("middleware 装配源码读取失败: %s @ %s", path, commit)
    return build_middleware_projection(sources)


def _build_tool_infos(commit: str | None) -> list[dict[str, Any]]:
    """扫 tools/ 目录，读每个 .py 的模块 docstring 首句 + 作用域标注。

    harness 的 tools/ 是全局平铺的，不存在 tool→agent 映射；每个文件的真实作用域
    各不相同（global/middleware/agent/memory）。作用域从 TOOL_SCOPE_MAP 查得，
    查不到填 {kind: "unknown"} 兜底，前端会显示"⚠ 未登记作用域"提醒补登记。

    与 middleware 投影的差异：描述只取 docstring 首句（需求 D8），且
    多一个 scope 字段；排除 __init__.py（包初始化不是 tool）。
    """
    if not commit:
        return []
    py_files = [
        f for f in _list_files_at_commit(commit, "tools")
        if f.endswith(".py") and not f.endswith("__init__.py")
    ]
    tools: list[dict[str, Any]] = []
    for f in py_files:
        name = f.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        description: str | None = None
        load_error: str | None = None
        try:
            src = show_file(commit, f)
            # 首句 = docstring 第一行（harness tool docstring 首行均为一句话概述）
            full_doc = ast.get_docstring(ast.parse(src))
            description = full_doc.split("\n", 1)[0].strip() if full_doc else None
        except Exception as e:  # noqa: BLE001
            logger.debug("tool docstring 解析失败: %s @ %s", f, commit)
            load_error = str(e)
        tools.append({
            "path": f,
            "name": name,
            "description": description,
            # 查不到作用域兜底 unknown，逼开发者补登记 TOOL_SCOPE_MAP
            "scope": TOOL_SCOPE_MAP.get(f, {"kind": "unknown"}),
            "load_error": load_error,
        })
    return tools


# ── 视图构建 ────────────────────────────────────────────────────


def build_elements_view(version: int) -> dict[str, Any]:
    """从 git 仓库构建版本要素展示视图（Agent 结构按版本布局探测）。

    结构（对齐前端 HarnessElementsView 类型）：
      {
        "version": int,
        "source_commit": str | None,
        "has_source": bool,
        "layout": "manifest" | "v7" | "v14",
        "agents": [ {name, kind, role, display_name, runtime_name,
                     prompt, skills, middlewares, tools}, ... ],
        "tools": [ {path, name, description, scope, load_error}, ... ],
        "subagent_relations": [ {from, to, role}, ... ],
        "unmounted": {subagents, middleware, prompts, skills}   # 仅 manifest 布局
      }

    布局优先级（REQ-20261006-130414 FR-001/009）：有架构清单 → 清单视图
    （唯一真相源，源码解析退役）；无清单回退 v7/v14 静态探测（历史版本兼容）。
    agents 顺序：清单 = 声明序（main 在前）；v7 = 故事专家→审查器；
    v14 = orchestrator→领域→审查器。tools 顶层平级——tools/ 全局平铺。
    """
    commit = _version_to_commit(version)
    manifest = _manifest_at_commit(commit)
    if manifest is not None:
        return _build_manifest_elements_view(version, commit, manifest)
    specs, layout = _agent_specs_for_commit(commit)
    skills = _build_skill_infos(commit)
    middleware_stacks = _build_middleware_stacks(commit)
    tools = _build_tool_infos(commit)

    agents = [
        {
            "name": name,
            "kind": kind,
            # role 派生（旧布局兼容清单版前端结构）：顶层装配 = main
            "role": "main" if kind in ("story_expert", "orchestrator") else "sub",
            "display_name": _SUBAGENT_ROLE_MAP.get(name, name),
            "runtime_name": name,
            "prompt": {"body": read_prompt_body(commit, prompt_files)},
            "skills": _agent_skills(skills, name, kind, layout),
            "middlewares": middleware_stacks.get(name, []),
            "tools": [],
        }
        for name, kind, prompt_files in specs
    ]

    if layout == "v14":
        # v14 委托关系：orchestrator → 3 领域 + 审查（全部经 task 委托调度）
        relations = [
            {"from": "orchestrator", "to": spec[0],
             "role": _SUBAGENT_ROLE_MAP.get(spec[0], spec[0])}
            for spec in specs if spec[1] == "domain"
        ]
        relations.append({
            "from": "orchestrator", "to": "storybuilding_review",
            "role": _SUBAGENT_ROLE_MAP["storybuilding_review"],
        })
    else:
        # v7 唯一委托关系：故事专家 → 审查器
        relations = [
            {"from": "storybuilding", "to": "storybuilding_review",
             "role": _SUBAGENT_ROLE_MAP["storybuilding_review"]},
        ]

    return {
        "version": version,
        "source_commit": commit,
        "has_source": commit is not None,
        "layout": layout,
        "agents": agents,
        "tools": tools,
        "subagent_relations": relations,
    }


def _build_manifest_elements_view(
    version: int, commit: str | None, manifest: ArchitectureManifest
) -> dict[str, Any]:
    """清单版要素视图（FR-006/007）：结构、挂载、委托关系全部以清单为准。"""
    skills = _build_skill_infos(commit)
    stacks = _manifest_middleware_stacks(commit, manifest)
    tools = _build_tool_infos(commit)
    tool_names = {t["name"] for t in tools}

    agents = [
        {
            "name": agent.name,
            "kind": agent.role,
            "role": agent.role,
            "display_name": agent.display_name or agent.name,
            "runtime_name": agent.runtime_name or agent.name,
            "description": agent.description,
            "prompt": {"body": read_prompt_body(commit, (agent.prompt,))},
            "skills": [s for s in skills
                       if any(s["path"] == f"skills/{name}"
                              or s["path"].startswith(f"skills/{name}/")
                              for name in agent.skills)],
            "middlewares": stacks.get(agent.name, []),
            "tools": [t for t in agent.tools if t in tool_names],
        }
        for agent in manifest.agents
    ]
    relations = [
        {"from": parent.name, "to": child,
         "role": (manifest.agent(child).display_name if manifest.agent(child) else child) or child}
        for parent in manifest.agents for child in parent.delegates
    ]
    return {
        "version": version,
        "source_commit": commit,
        "has_source": commit is not None,
        "layout": "manifest",
        "agents": agents,
        "tools": tools,
        "subagent_relations": relations,
        "unmounted": _commit_unmounted(commit, manifest),
    }


def build_memory_elements_view(version: int) -> dict[str, Any]:
    """构建记忆子系统要素视图（NWM 6 要素）。

    记忆要素横跨 prompts/middleware/tools 三目录、不属于任何 agent，故独立于
    build_elements_view（按 agent 分组）。只返回该版本实际存在的文件——老版本可能
    还没有 NWM 重构，此时 elements 为空，前端显示"此版本无记忆子系统"。

    结构（对齐前端 MemoryElementsView 类型）：
      {
        "version": int,
        "has_source": bool,
        "elements": [ {name, path, type, file_role, description, tags}, ... ]
      }
    elements 按 MEMORY_ROLE_ORDER 排序（抽取→存储→检索→回填）。
    """
    commit = _version_to_commit(version)
    elements: list[dict[str, Any]] = []

    if commit:
        for path, (f_type, file_role, description) in MEMORY_FILES.items():
            # 检查文件在该 commit 是否存在（show_file 失败即不存在）
            if not _file_exists_at_commit(commit, path):
                continue
            name = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            elements.append({
                "name": name,
                "path": path,
                "type": f_type,
                "file_role": file_role,
                "description": description,
                "tags": ["memory"],
            })

        # 按协同链顺序排序（抽取→存储→检索→回填）
        role_index = {r: i for i, r in enumerate(MEMORY_ROLE_ORDER)}
        elements.sort(key=lambda e: role_index.get(e["file_role"], 99))

    return {
        "version": version,
        "has_source": commit is not None,
        "elements": elements,
    }


def _file_exists_at_commit(commit: str, path: str) -> bool:
    """检查某文件在某 commit 是否存在（git show 成功即存在）。"""
    try:
        show_file(commit, path)
        return True
    except Exception:  # noqa: BLE001
        return False


# ── 端点 ────────────────────────────────────────────────────────


@router.get("/{version}/harness-elements/memory")
def get_memory_elements(version: int) -> dict[str, Any]:
    """记忆子系统要素视图（NWM 6 要素）。version 不存在则 404。

    与 /harness-elements 独立——记忆要素横跨三目录不属于任何 agent，集中返回。
    老版本无 NWM 重构时 elements 为空（非 404）。
    """
    _require_ledger_version(version)
    return _cached_build(version, _memory_cache, lambda: build_memory_elements_view(version))


@router.get("/{version}/harness-elements")
def get_elements(version: int) -> dict[str, Any]:
    """Harness 要素展示视图（从 git 源文件读取）。version 不存在则 404。

    热路径：前端 harness 页进页面 + 切版本都会打这里。按 version 进程内缓存
    （commit 不可变，安全），避免每次 fork 几十个 git show 子进程。
    """
    _require_ledger_version(version)
    return _cached_build(version, _elements_cache, lambda: build_elements_view(version))


@router.get("/{version}/source")
def get_source(
    version: int,
    path: str = Query(..., description="相对 harness 包根的文件路径，如 middleware/goal.py"),
) -> dict[str, Any]:
    """读指定版本指定文件的源码全文（middleware 懒加载用）。"""
    try:
        commit = _version_to_commit(version)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not commit:
        raise HTTPException(status_code=404, detail=f"版本 v{version} 无可执行 commit 绑定")

    try:
        content = show_file(commit, path)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "does not exist" in msg or "exists on disk, but not in" in msg:
            raise HTTPException(status_code=404, detail=f"{path} 在 v{version} 不存在")
        logger.warning("源码读取失败: %s @ v%s → %s", path, version, msg)
        raise HTTPException(status_code=500, detail=f"源码读取失败: {msg}")

    return {"path": path, "content": content}
