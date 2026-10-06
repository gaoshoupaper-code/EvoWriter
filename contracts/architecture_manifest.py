"""架构清单契约（REQ-20261006-130414 FR-001/002，DEC-007 M2 完整清单）。

architecture.json 是 harness 包的装配真相源：声明全部 agent 及其静态挂载
（prompt 路径、有序 domain middleware 栈、skills、委托关系、写入权限）。
三个消费方共用本模块，规则唯一：

  - executor：清单解释器按清单装配（app.platform.agent.architecture）
  - evolution：validate_changes 对清单做强校验（CON-002 隔离——进化 Agent
    不可修改本文件，校验规则对进化侧不可篡改）
  - evolution elements_api：桌面要素视图直接读清单投影（源码解析退役）

静态/动态边界（DEC-007）：清单只管「挂什么」；运行时才能确定的逻辑
（按需求配比算预算、风格 suffix 注入、ctx 相关回调）留在装配代码。

纯静态校验，不 import 业务代码：middleware build 钩子存在性用 AST 检查。
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

# 清单文件在 harness 包根的固定文件名（进化点 target 指向它 = 架构级改动）
ARCHITECTURE_MANIFEST_FILENAME = "architecture.json"

# 清单 schema 版本（结构不兼容变更时递增）
ARCHITECTURE_SCHEMA = "writer.architecture/1"

# 子代理数量上限（DEC-006 清单强校验；默认值实现期冻结为 8）
MAX_SUBAGENTS = 8

# 解释器固有的基础中间件链（DEC-007 静态/动态边界）：
# 全 agent 统一、按 ctx 接线（trace 回调/快照回调/重放策略）的平台不变量，
# 不属 agent 级挂载面——不进清单 middleware 列表，也不计入未挂载孤儿报告。
BASE_CHAIN_MODULES = (
    "error_recovery",
    "read_cache",
    "path_guard",
    "encoding_guard",
    "file_state_tracker",
    "file_write_serialize",
    "write_result_inspector",
    "artifact_snapshot",
)

# 审查闭环模式模块（解释器对有委托的 agent 固定追加，RevisionLimit 硬上限），
# 同样不属于 agent 级挂载面，孤儿报告一并豁免。
REVIEW_PATTERN_MODULES = ("revision_limit",)

# 孤儿报告豁免全集（基础链 + 审查模式）
UNMOUNTED_EXEMPT_MODULES = BASE_CHAIN_MODULES + REVIEW_PATTERN_MODULES

# 合法 agent 名：字母数字下划线连字符（进 trace 名、模块名，收紧字符集）
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_\-]+$")


class ManifestError(ValueError):
    """清单缺失 / 解析失败 / schema 不合法。"""


class AgentSpec(BaseModel):
    """单个 agent 的静态挂载声明。

    name            架构名（唯一标识，桌面展示与委托关系用）
    role            main = 顶层装配（executor 直接运行）；sub = 被委托子代理
    display_name    桌面展示中文名
    description     委托描述（父 agent 据此选择委托目标；task 工具可见）
    runtime_name    运行时委托名（默认 = name；仅当父 prompt 按旧名引用时才覆写）
    prompt          prompt 文件路径（相对包根）
    middleware      domain middleware 有序列表（外→内；对应 middleware/{name}.py）
    skills          skill 目录名列表（对应 skills/{name}/）
    tools           挂载的工具名列表（对应 tools/{name}.py；空 = 无专属工具）
    delegates       本 agent 委托出去的子代理架构名列表
    delegated_by    委托方架构名（role=sub 必填；与对方 delegates 双向一致）
    context_files   运行时注入 workspace 文件列表（相对 workspace 根，如 demand.md）
    write_permissions  写入权限 glob 列表（相对 workspace 根，如 "/storyline.md"）
    definition      动态钩子定义模块路径（可选；纯清单 agent 不需要）
    artifact_paths  产物校验文件列表（相对 workspace 根；ArtifactValidation 用）
    max_revisions   审查修订上限（挂 delegates 的 agent 生效；默认 1）
    """

    name: str
    role: str = Field(pattern=r"^(main|sub)$")
    display_name: str = ""
    description: str = ""
    runtime_name: str = ""
    prompt: str
    middleware: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    delegates: list[str] = Field(default_factory=list)
    delegated_by: str | None = None
    context_files: list[str] = Field(default_factory=list)
    write_permissions: list[str] = Field(default_factory=list)
    definition: str | None = None
    artifact_paths: list[str] = Field(default_factory=list)
    max_revisions: int = 1


class ArchitectureManifest(BaseModel):
    """harness 包架构清单根对象。"""

    schema_version: str = ARCHITECTURE_SCHEMA
    agents: list[AgentSpec] = Field(min_length=1)

    def agent(self, name: str) -> AgentSpec | None:
        return next((a for a in self.agents if a.name == name), None)

    @property
    def main_agents(self) -> list[AgentSpec]:
        return [a for a in self.agents if a.role == "main"]

    @property
    def sub_agents(self) -> list[AgentSpec]:
        return [a for a in self.agents if a.role == "sub"]


class ManifestValidationResult(BaseModel):
    """静态校验结果。errors 非空 = 阻断（不得发版）；unmounted 供桌面展示。"""

    errors: list[str] = Field(default_factory=list)
    unmounted_subagents: list[str] = Field(default_factory=list)
    unmounted_middleware: list[str] = Field(default_factory=list)
    unmounted_prompts: list[str] = Field(default_factory=list)
    unmounted_skills: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def manifest_path(pkg_dir: Path) -> Path:
    return Path(pkg_dir) / ARCHITECTURE_MANIFEST_FILENAME


def load_architecture_manifest(pkg_dir: Path) -> ArchitectureManifest:
    """加载并做 schema 级校验（结构合法性；引用/环等业务校验见 validate）。

    Raises:
        ManifestError: 文件缺失 / JSON 解析失败 / schema 不匹配。
    """
    path = manifest_path(pkg_dir)
    if not path.is_file():
        raise ManifestError(f"架构清单不存在: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"架构清单读取/解析失败: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != ARCHITECTURE_SCHEMA:
        raise ManifestError(
            f"架构清单 schema 不匹配: 期望 {ARCHITECTURE_SCHEMA}，"
            f"实际 {raw.get('schema_version') if isinstance(raw, dict) else type(raw).__name__}"
        )
    try:
        return ArchitectureManifest.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError 细节收敛
        raise ManifestError(f"架构清单结构不合法: {exc}") from exc


def _module_has_build(py_file: Path) -> bool:
    """AST 检查模块是否定义顶层 build(ctx) 工厂（不 import，纯静态）。"""
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "build":
            return True
    return False


def _safe_rel_path(path: str) -> bool:
    """相对路径且无穿越（清单内所有包内路径必须满足）。"""
    return bool(path) and not path.startswith("/") and ".." not in path.split("/")


def _safe_glob(directory: Path, pattern: str, *, recursive: bool = False) -> list[Path]:
    """目录缺失返回空（最小包可能没有 skills/ 等子目录）。"""
    if not directory.is_dir():
        return []
    return list(directory.rglob(pattern) if recursive else directory.glob(pattern))


def _safe_iterdir(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return list(directory.iterdir())


def _find_delegation_cycle(manifest: ArchitectureManifest) -> list[str] | None:
    """全委托图环检测（三色 DFS）。返回环路径（首尾同名），无环返回 None。

    迭代实现：栈存 (节点, 已展开到的边下标)，on_stack 集合标当前路径，
    回边命中 on_stack 即成环。
    """
    graph = {a.name: list(a.delegates) for a in manifest.agents}
    color = {name: 0 for name in graph}  # 0=未访问 1=路径上 2=完成
    parent: dict[str, str] = {}

    for root in graph:
        if color[root]:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        color[root] = 1
        while stack:
            node, idx = stack[-1]
            edges = graph.get(node, [])
            if idx >= len(edges):
                color[node] = 2
                stack.pop()
                continue
            stack[-1] = (node, idx + 1)
            nxt = edges[idx]
            if color.get(nxt, 2) == 1:
                # 回边：沿 parent 回溯出环路径
                cycle = [nxt, node]
                cur = node
                while cur != nxt:
                    cur = parent[cur]
                    cycle.append(cur)
                return list(reversed(cycle))
            if color.get(nxt, 2) == 0:
                color[nxt] = 1
                parent[nxt] = node
                stack.append((nxt, 0))
    return None


def validate_architecture_manifest(
    pkg_dir: Path, manifest: ArchitectureManifest
) -> ManifestValidationResult:
    """清单强校验（FR-002）：引用存在、双向委托一致、无环、数量上限、build 钩子。

    包内路径引用错误、委托不一致、超上限都进 errors（阻断发版）；
    存在但未被清单引用的要素文件进 unmounted_*（不阻断，桌面显示「未挂载」）。
    """
    pkg_dir = Path(pkg_dir)
    result = ManifestValidationResult()
    names = {a.name for a in manifest.agents}

    # ── agent 级检查 ──────────────────────────────────────────
    if len(manifest.main_agents) != 1:
        result.errors.append(
            f"清单必须恰好声明一个 role=main 的顶层 agent（实际 {len(manifest.main_agents)} 个，"
            f"executor 装配产物是单一顶层图）"
        )
    for agent in manifest.agents:
        if not _SAFE_NAME.match(agent.name):
            result.errors.append(f"agent 名不合法 '{agent.name}'（只允许字母数字下划线连字符）")
        if not _safe_rel_path(agent.prompt):
            result.errors.append(f"[{agent.name}] prompt 路径不合法: {agent.prompt}")
        if agent.role == "sub" and not agent.delegated_by:
            result.errors.append(f"[{agent.name}] role=sub 但缺 delegated_by")
        if agent.role == "main" and agent.delegated_by:
            result.errors.append(f"[{agent.name}] role=main 不应设置 delegated_by")
        # 文件存在性
        if _safe_rel_path(agent.prompt) and not (pkg_dir / agent.prompt).is_file():
            result.errors.append(f"[{agent.name}] prompt 文件不存在: {agent.prompt}")
        if agent.definition:
            if not _safe_rel_path(agent.definition):
                result.errors.append(f"[{agent.name}] definition 路径不合法: {agent.definition}")
            elif not (pkg_dir / agent.definition).is_file():
                result.errors.append(f"[{agent.name}] definition 文件不存在: {agent.definition}")
        for mw in agent.middleware:
            mw_file = pkg_dir / "middleware" / f"{mw}.py"
            if not mw_file.is_file():
                result.errors.append(f"[{agent.name}] middleware 文件不存在: middleware/{mw}.py")
            elif not _module_has_build(mw_file):
                result.errors.append(
                    f"[{agent.name}] middleware/{mw}.py 缺少顶层 build(ctx) 工厂"
                )
        for skill in agent.skills:
            if not (pkg_dir / "skills" / skill / "SKILL.md").is_file():
                result.errors.append(f"[{agent.name}] skill 目录不存在或无 SKILL.md: skills/{skill}")
        for tool in agent.tools:
            if not (pkg_dir / "tools" / f"{tool}.py").is_file():
                result.errors.append(f"[{agent.name}] tool 文件不存在: tools/{tool}.py")

    # ── 委托关系：双向一致 + 环检测 ─────────────────────────────
    for agent in manifest.agents:
        for target in agent.delegates:
            if target not in names:
                result.errors.append(f"[{agent.name}] delegates 指向不存在的 agent: {target}")
                continue
            target_spec = manifest.agent(target)
            if target_spec and target_spec.delegated_by != agent.name:
                result.errors.append(
                    f"[{agent.name}] delegates→{target}，但对方 delegated_by="
                    f"{target_spec.delegated_by!r}（双向不一致）"
                )
        if agent.delegated_by and agent.delegated_by not in names:
            result.errors.append(f"[{agent.name}] delegated_by 指向不存在的 agent: {agent.delegated_by}")

    # 环检测：全委托图三色 DFS（只沿首边走会漏检非首位委托构成的环）
    cycle = _find_delegation_cycle(manifest)
    if cycle:
        result.errors.append(f"委托环: {' → '.join(cycle)}")

    # ── 数量上限（DEC-006）────────────────────────────────────
    if len(manifest.sub_agents) > MAX_SUBAGENTS:
        result.errors.append(
            f"子代理数量超上限: {len(manifest.sub_agents)} > {MAX_SUBAGENTS}"
        )

    # ── 孤儿报告（不阻断；DEC-005 桌面显示「未挂载」）────────────
    # 目录缺失（最小包可能没有 skills/ 等）按空处理，不算错误。
    referenced_mw = {mw for a in manifest.agents for mw in a.middleware}
    referenced_skills = {s for a in manifest.agents for s in a.skills}
    referenced_prompts = {a.prompt for a in manifest.agents}
    referenced_defs = {a.definition for a in manifest.agents if a.definition}

    result.unmounted_middleware = sorted(
        f.name[:-3]
        for f in _safe_glob(pkg_dir / "middleware", "*.py")
        if f.name != "__init__.py"
        and f.name[:-3] not in referenced_mw
        and f.name[:-3] not in UNMOUNTED_EXEMPT_MODULES
    )
    result.unmounted_skills = sorted(
        d.name
        for d in _safe_iterdir(pkg_dir / "skills")
        if d.is_dir() and d.name not in referenced_skills
    )
    result.unmounted_prompts = sorted(
        f.relative_to(pkg_dir).as_posix()
        for f in _safe_glob(pkg_dir / "prompts", "*.md")
        if f.relative_to(pkg_dir).as_posix() not in referenced_prompts
    )
    result.unmounted_subagents = sorted(
        f.relative_to(pkg_dir).as_posix()
        for f in _safe_glob(pkg_dir / "subagents", "*.py", recursive=True)
        if f.name != "__init__.py" and f.relative_to(pkg_dir).as_posix() not in referenced_defs
    )

    return result


def load_and_validate(pkg_dir: Path) -> tuple[ArchitectureManifest, ManifestValidationResult]:
    """加载 + 全量校验（evolution validate_changes / elements_api 共用入口）。"""
    manifest = load_architecture_manifest(pkg_dir)
    return manifest, validate_architecture_manifest(pkg_dir, manifest)


__all__ = [
    "ARCHITECTURE_MANIFEST_FILENAME",
    "ARCHITECTURE_SCHEMA",
    "BASE_CHAIN_MODULES",
    "MAX_SUBAGENTS",
    "REVIEW_PATTERN_MODULES",
    "UNMOUNTED_EXEMPT_MODULES",
    "AgentSpec",
    "ArchitectureManifest",
    "ManifestError",
    "ManifestValidationResult",
    "load_and_validate",
    "load_architecture_manifest",
    "manifest_path",
    "validate_architecture_manifest",
]
