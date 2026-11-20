"""Project versioned harness middleware assembly without executing its code."""
from __future__ import annotations

import ast
import hashlib
from collections.abc import Iterable, Iterator
from typing import Any

HOOK_ORDER = (
    "before_agent",
    "before_model",
    "wrap_model_call",
    "after_model",
    "wrap_tool_call",
    "after_agent",
)

_RUNTIME_MIDDLEWARE: dict[str, dict[str, Any]] = {
    "PlatformArtifactCaptureMiddleware": {
        "hooks": ["wrap_tool_call"],
        "description": "记录当前写作工具实际保存的产物及其修订。",
    },
    "InstructionScopeMiddleware": {
        "hooks": ["wrap_tool_call"],
        "description": "按本套任务的目标、写入职责与已登记指令校验工具调用。",
    },
    "TraceMiddleware": {
        "hooks": ["wrap_model_call", "wrap_tool_call"],
        "description": "记录模型与工具调用的 Trace 事件。",
    },
    "CreditsMiddleware": {
        "hooks": ["wrap_model_call", "wrap_tool_call"],
        "description": "在用户创作运行中执行积分预扣与结算。",
    },
    "ContextAssemblerMiddleware": {
        "hooks": ["before_model", "wrap_model_call"],
        "description": "在模型调用前装配该 Agent 所需的文件上下文。",
    },
    "ArtifactValidationMiddleware": {
        "hooks": ["after_model"],
        "description": "在 Agent 结束前校验必需产物是否已经生成。",
    },
}


def build_middleware_projection(
    package_sources: dict[str, str],
) -> dict[str, list[dict[str, Any]]]:
    """Return the mounted middleware stack for every visible Agent.

    布局自适应（REQ-20260923-145931 后续：v14 多 Agent 形态适配）：
    - v14 多 Agent 架构（subagents/orchestrator.py 存在）：orchestrator +
      3 领域代理 + reviewer 五泳道。middleware_factory(agent_name) 产出通用
      骨架；orchestrator 追加 QuotaConvergence/RevisionLimit/ContextAssembler，
      storyline 追加单线护栏，worldview/character 为纯通用骨架。
    - v7 单故事专家架构（旧版本）：两泳道（storybuilding + reviewer）。
    """
    catalog, _ = _build_catalog(package_sources)
    root = package_sources.get("__init__.py", "")

    # 两代架构共用：__init__.py 的 middleware_factory 产出各 agent 通用骨架。
    common = _extract_stack(root, "middleware_factory", "mw", group="base")

    if package_sources.get("subagents/orchestrator.py"):
        return _project_v14(package_sources, root, common, catalog)
    return _project_v7(package_sources, root, common, catalog)


def _project_v14(
    package_sources: dict[str, str],
    root: str,
    common: list[dict[str, Any]] | None,
    catalog: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """v14 多 Agent 架构投影（orchestrator + worldview/character/storyline + reviewer）。"""
    orchestrator_source = package_sources["subagents/orchestrator.py"]
    orchestrator = _extract_stack(
        orchestrator_source, "build_orchestrator_agent", "orchestrator_mw",
        base=common, group="agent",
    )
    orchestrator = _resolve_conditional_mount(
        orchestrator,
        "ContextAssemblerMiddleware",
        # __init__.assemble 调 build_orchestrator_agent 时传 context_file_paths
        # （demand.md 表单直入）→ ContextAssembler 实挂
        _call_supplies_keyword(root, "build_orchestrator_agent", "context_file_paths"),
    )
    storyline = _extract_stack(
        orchestrator_source, "build_orchestrator_agent", "storyline_mw",
        base=common, group="agent",
    )
    review_storybuilding = _extract_stack(
        package_sources.get("subagents/reviewers/storybuilding.py", ""),
        "build_storybuilding_reviewer",
        "review_middleware",
        base=common,
        group="agent",
    )
    # 五泳道：领域代理中仅 storyline 追加护栏，worldview/character 为纯通用骨架
    stacks = {
        "orchestrator": orchestrator,
        "worldview": common,
        "character": common,
        "storyline": storyline,
        "storybuilding_review": review_storybuilding,
    }
    return {
        agent: [_enrich_mount(mount, catalog.get(mount["class_name"], {})) for mount in mounts]
        for agent, mounts in stacks.items()
        if mounts is not None
    }


def _project_v7(
    package_sources: dict[str, str],
    root: str,
    common: list[dict[str, Any]] | None,
    catalog: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """v7 单故事专家架构投影（两泳道，含 v7 之前的历史形态）。"""
    storybuilding = _extract_stack(
        package_sources.get("subagents/storybuilding.py", ""),
        "build_storybuilding_deep_subagent",
        "storybuilding_middleware",
        base=common,
        group="agent",
    )
    storybuilding = _resolve_conditional_mount(
        storybuilding,
        "ContextAssemblerMiddleware",
        _call_supplies_keyword(root, "build_storybuilding_deep_subagent", "context_file_paths"),
    )

    factory_source = package_sources.get("subagents/factory.py", "")
    factory_stack = _extract_stack(
        factory_source, "build_deep_subagent", "mw", base=storybuilding, group="agent"
    )
    if factory_stack is not None:
        storybuilding = factory_stack
    storybuilding = _resolve_conditional_mount(
        storybuilding,
        "ArtifactValidationMiddleware",
        _call_supplies_keyword(
            package_sources.get("subagents/storybuilding.py", ""),
            "build_deep_subagent",
            "artifact_paths",
        ),
    )
    review_storybuilding = _extract_stack(
        package_sources.get("subagents/reviewers/storybuilding.py", ""),
        "build_storybuilding_reviewer",
        "review_middleware",
        base=common,
        group="agent",
    )
    # v7 两泳道：故事专家（storybuilding，顶层）+ 审查器（storybuilding_review）
    stacks = {
        "storybuilding": storybuilding,
        "storybuilding_review": review_storybuilding,
    }
    return {
        agent: [_enrich_mount(mount, catalog.get(mount["class_name"], {})) for mount in mounts]
        for agent, mounts in stacks.items()
        if mounts is not None
    }


def _build_catalog(
    package_sources: dict[str, str],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    catalog = {name: dict(info) for name, info in _RUNTIME_MIDDLEWARE.items()}
    modules: dict[str, dict[str, Any]] = {}
    for path, source in package_sources.items():
        if not path.startswith("middleware/") or not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        candidates = [name for name in classes if name.endswith("Middleware")]
        built_class = _factory_class(tree, classes)
        if built_class is None and len(candidates) == 1:
            built_class = candidates[0]
        if built_class and built_class not in candidates:
            candidates.append(built_class)
        description = ast.get_docstring(tree)
        for name in candidates:
            hook_descriptions = _class_hooks(classes[name], classes)
            info = {
                "class_name": name,
                "source_path": path,
                "description": description or ast.get_docstring(classes[name]),
                "hooks": [hook for hook in HOOK_ORDER if hook in hook_descriptions],
                "hook_descriptions": hook_descriptions,
            }
            catalog[name] = info
            if name == built_class:
                modules[path] = info
    return catalog, modules


def _factory_returns(nodes: Iterable[ast.AST]) -> Iterator[ast.expr | None]:
    for node in nodes:
        if isinstance(node, ast.Return):
            yield node.value
        elif not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield from _factory_returns(ast.iter_child_nodes(node))


def _factory_class(tree: ast.Module, classes: dict[str, ast.ClassDef]) -> str | None:
    factory = next((node for node in reversed(tree.body)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "build"), None)
    if factory is None:
        return None
    targets = {
        value.func.id if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) else None
        for value in _factory_returns(factory.body)
    }
    if len(targets) == 1:
        target, = targets
        if target is not None and target in classes:
            return target
    return None


def _class_hooks(node: ast.ClassDef, classes: dict[str, ast.ClassDef]) -> dict[str, str | None]:
    hooks: dict[str, str | None] = {}
    visited: set[str] = set()
    pending = [node]
    while pending:
        current = pending.pop()
        if current.name in visited:
            continue
        visited.add(current.name)
        declared: dict[str, str | None] = {}
        for item in current.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = item.name if item.name in HOOK_ORDER else item.name.removeprefix("a")
                if name in HOOK_ORDER:
                    declared[name] = declared.get(name) or ast.get_docstring(item)
        for hook, description in declared.items():
            hooks.setdefault(hook, description)
        pending.extend(classes[base.id] for base in reversed(current.bases)
                       if isinstance(base, ast.Name) and base.id in classes)
    return hooks


def _call_supplies_keyword(source: str, function_name: str, keyword_name: str) -> bool:
    if not source:
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return any(
        keyword.arg == keyword_name
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _expr_name(node.func) == function_name
        for keyword in node.keywords
    )


def _resolve_conditional_mount(
    stack: list[dict[str, Any]] | None,
    class_name: str,
    enabled: bool,
) -> list[dict[str, Any]] | None:
    if stack is None:
        return None
    if not enabled:
        return [mount for mount in stack if mount["class_name"] != class_name]
    return [
        {**mount, "optional": False} if mount["class_name"] == class_name else mount
        for mount in stack
    ]


def _extract_stack(
    source: str,
    function_name: str,
    variable_name: str,
    *,
    base: list[dict[str, Any]] | None = None,
    group: str,
) -> list[dict[str, Any]] | None:
    fallback = [dict(mw) for mw in base] if base else None
    if not source:
        return fallback
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return fallback
    function = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ),
        None,
    )
    if function is None:
        return fallback

    stack: list[dict[str, Any]] = []
    initialized = False

    def visit(statements: list[ast.stmt], conditional: bool = False) -> None:
        nonlocal stack, initialized
        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                target = statement.targets[0] if isinstance(statement, ast.Assign) else statement.target
                value = statement.value
                if isinstance(target, ast.Name) and target.id == variable_name and value is not None:
                    initial = _initial_stack(value, base, group)
                    stack = initial or []
                    initialized = initial is not None
                    continue
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                call = statement.value
                if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
                    if call.func.value.id == variable_name:
                        if call.func.attr == "append" and call.args:
                            mount = _mount_from_expr(call.args[0], group, conditional)
                            if mount:
                                stack.append(mount)
                        elif call.func.attr == "insert" and len(call.args) >= 2:
                            mount = _mount_from_expr(call.args[1], group, conditional)
                            if mount:
                                index = _literal(call.args[0])
                                stack.insert(index if isinstance(index, int) else len(stack), mount)
                        elif call.func.attr == "extend" and call.args and base:
                            stack.extend(dict(mw) for mw in base)
            if isinstance(statement, ast.If):
                visit(statement.body, True)
                visit(statement.orelse, True)
            elif isinstance(statement, (ast.For, ast.While, ast.With, ast.Try)):
                for field in ("body", "orelse", "finalbody"):
                    nested = getattr(statement, field, None)
                    if nested:
                        visit(nested, conditional)

    visit(function.body)
    return stack if initialized or stack else fallback


def _initial_stack(
    expression: ast.expr,
    base: list[dict[str, Any]] | None,
    group: str,
) -> list[dict[str, Any]] | None:
    if isinstance(expression, ast.List):
        mounts = [
            mount
            for item in expression.elts
            if (mount := _mount_from_expr(item, group, False)) is not None
        ]
        return mounts if mounts or not expression.elts else None
    if base is None:
        return None
    if isinstance(expression, ast.ListComp):
        excluded = {
            node.id
            for node in ast.walk(expression)
            if isinstance(node, ast.Name) and node.id.endswith("Middleware")
        }
        return [dict(mw) for mw in base if mw["class_name"] not in excluded]
    if isinstance(expression, ast.IfExp):
        return [dict(mw) for mw in base]
    if isinstance(expression, ast.Call) and _expr_name(expression.func) == "list":
        return [dict(mw) for mw in base]
    return None


def _mount_from_expr(
    expression: ast.expr,
    group: str,
    optional: bool,
) -> dict[str, Any] | None:
    call = expression if isinstance(expression, ast.Call) else None
    class_name = _class_name(call.func if call else expression)
    if not class_name:
        return None
    params: dict[str, Any] = {}
    if call:
        for keyword in call.keywords:
            if keyword.arg is None:
                continue
            value = _literal(keyword.value)
            if value is not None:
                params[keyword.arg] = value
    return {
        "class_name": class_name,
        "params": params,
        "group": "runtime" if class_name in {"TraceMiddleware", "CreditsMiddleware"} else group,
        "optional": optional,
    }


def _class_name(expression: ast.expr) -> str | None:
    name = _expr_name(expression)
    if not name:
        return None
    if name.endswith("Middleware"):
        return name
    if name.endswith("_middleware_cls"):
        return _snake_to_pascal(name.removesuffix("_cls"))
    if name.endswith("_middleware"):
        return _snake_to_pascal(name)
    return None


def _expr_name(expression: ast.expr) -> str | None:
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        return expression.attr
    return None


def _snake_to_pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


def _literal(expression: ast.expr) -> Any | None:
    try:
        return ast.literal_eval(expression)
    except (ValueError, TypeError):
        return None


def _enrich_mount(
    mount: dict[str, Any],
    info: dict[str, Any],
) -> dict[str, Any]:
    class_name = mount["class_name"]
    hooks = info.get("hooks", [])
    description = info.get("description")
    hook_details = {}
    for hook in hooks:
        details = _HOOK_DETAILS.get((class_name, hook))
        if details:
            trigger, action, effect = details
        else:
            hook_description = info.get("hook_descriptions", {}).get(hook)
            if not hook_description and len(hooks) == 1:
                hook_description = description
            trigger, effect = None, None
            action = hook_description.splitlines()[0] if hook_description else None
        hook_details[hook] = {"trigger": trigger, "action": action, "effect": effect}
    return {
        **mount,
        # `hook` keeps already-installed desktop clients functional; new clients use
        # `hooks` so a middleware can appear under each stage where it acts.
        "hook": hooks[0] if hooks else None,
        "hooks": hooks,
        "hook_details": hook_details,
        "source_path": info.get("source_path", mount.get("source_path")),
        "description": description,
    }


def project_manifest_stacks(
    package_sources: dict[str, str],
    manifest: "Any",
) -> dict[str, list[dict[str, Any]]]:
    """架构清单版栈投影（REQ-20261006-130414 FR-001）。

    结构来自清单声明（真相源），hook 信息来自 middleware 源码目录的 catalog
    ——源码解析只服务展示层的类信息，不再推断挂载关系。栈构成与解释器装配
    顺序一致（executor app.platform.agent.architecture）：
      base 组（BASE_CHAIN_MODULES，全 agent 统一）→ Trace（runtime）→
      agent 组（清单 domain middleware 有序）→ runtime 组
      （ContextAssembler / RevisionLimit / ArtifactValidation 按清单条件）。
    """
    from contracts.architecture_manifest import BASE_CHAIN_MODULES

    catalog, modules = _build_catalog(package_sources)

    def _mount(module: str, group: str, optional: bool = False) -> dict[str, Any]:
        path = f"middleware/{module}.py"
        return {
            "class_name": modules.get(path, {}).get("class_name", module),
            "source_path": path,
            "params": {},
            "group": group,
            "optional": optional,
        }

    stacks: dict[str, list[dict[str, Any]]] = {}
    for agent in manifest.agents:
        mounts: list[dict[str, Any]] = []
        for module in BASE_CHAIN_MODULES:
            mounts.append(_mount(module, "base", optional=module == "artifact_snapshot"))
        mounts.insert(1, {"class_name": "TraceMiddleware", "params": {}, "group": "runtime", "optional": True})
        for module in agent.middleware:
            mounts.append(_mount(module, "agent"))
        compiled = agent.role == "main" or bool(agent.delegates)
        if compiled and agent.context_files:
            mounts.append({"class_name": "ContextAssemblerMiddleware", "params": {}, "group": "runtime", "optional": False})
        if compiled:
            mounts.append(_mount("revision_limit", "agent"))
        if compiled and agent.artifact_paths:
            mounts.append({"class_name": "ArtifactValidationMiddleware", "params": {}, "group": "runtime", "optional": False})
        if any(row.role == "main" and row.name in {"outline", "volume", "chapter", "text"} for row in manifest.agents):
            mounts[:0] = [
                {"class_name": name, "params": {}, "group": "runtime", "optional": False}
                for name in ("PlatformArtifactCaptureMiddleware", "InstructionScopeMiddleware")
            ]
        stack = [
            _enrich_mount(mount, modules.get(mount["source_path"], {}) if "source_path" in mount
                          else catalog.get(mount["class_name"], {}))
            for mount in mounts
        ]
        for position, row in enumerate(stack, start=1):
            source = package_sources.get(row["source_path"])
            row["position"] = position
            row["rule_version"] = hashlib.sha256(source.encode()).hexdigest() if source is not None else None
            row["condition"] = _CONDITIONS.get(row["class_name"], row["description"])
        stacks[agent.name] = stack
    return stacks


__all__ = ["HOOK_ORDER", "build_middleware_projection", "project_manifest_stacks"]

# Each entry describes the action at one hook. Only hooks found in the selected
# version are shown, even if descriptions are available for other hooks.
_HOOK_DETAILS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("RevisionLimitMiddleware", "before_agent"): (
        "本次 Agent 运行开始。", "重置本轮的审查次数。", "上一轮的审查次数不会占用本轮额度。",
    ),
    ("RevisionLimitMiddleware", "wrap_tool_call"): (
        "工具请求委托一个设有次数上限的审查助手。", "按审查助手计数，拦截超过上限的审查。",
        "助手在额度内继续审查，超限时收到停止委托的提示。",
    ),
    ("TextCompletionMiddleware", "before_model"): (
        "正文任务准备调用模型。", "更新正文的保存与审稿状态。", "本轮交付检查会使用最新记录。",
    ),
    ("TextCompletionMiddleware", "after_model"): (
        "模型准备交付当前正文。", "检查正文是否已保存和审稿，缺步骤时要求补齐。",
        "交付条件不足时暂缓交付，纠正次数用尽后反馈未完成原因。",
    ),
    ("ToolCallRecoveryMiddleware", "after_model"): (
        "正文模型把工具调用写成普通文字。", "要求模型改为真正调用工具。", "最多纠正两次，仍失败时停止本轮并提示。",
    ),
    ("TraceMiddleware", "wrap_model_call"): (
        "本轮启用运行记录，且模型被调用。", "记录模型请求、回复与调用结果。", "可在运行记录中查看模型怎样处理本轮任务。",
    ),
    ("TraceMiddleware", "wrap_tool_call"): (
        "本轮启用运行记录，且工具被调用。", "记录工具名称、参数与调用结果。", "可查出哪些工具执行过以及执行结果。",
    ),
    ("CreditsMiddleware", "wrap_model_call"): (
        "已预扣积分的创作收到模型用量。", "把模型用量换算为积分并累计。", "达到扣费上限时结算并停止创作。",
    ),
    ("CreditsMiddleware", "wrap_tool_call"): (
        "工具首次委托正式创作，且本轮尚未预扣。", "在创作开始前预扣积分。", "积分不足时阻止这次创作开始。",
    ),
    ("ContextAssemblerMiddleware", "before_model"): (
        "清单指定了任务输入文件，且模型即将调用。", "读取任务所需的资料文件。", "准备好本轮的文件上下文。",
    ),
    ("ContextAssemblerMiddleware", "wrap_model_call"): (
        "模型发起请求时已装配资料。", "把任务资料加入发给模型的请求。", "模型可以参考当前任务的文件内容。",
    ),
    ("ArtifactValidationMiddleware", "after_model"): (
        "模型准备结束，清单指定了必交产物。", "检查必须交付的产物是否齐全。", "缺少产物时反馈缺项。",
    ),
    ("StageGateMiddleware", "wrap_tool_call"): (
        "创作工具请求写入产物。", "检查产物是否在当前创作阶段的职责范围内。", "拦住越权写入，避免改错产物。",
    ),
    ("InstructionScopeMiddleware", "wrap_tool_call"): (
        "本套创作任务调用工具。", "核对目标卷章、允许写入的产物和已登记指令。", "拒绝超出本次任务范围的操作。",
    ),
    ("PlatformArtifactCaptureMiddleware", "wrap_tool_call"): (
        "写作工具完成保存。", "记录实际保存的产物和修订。", "运行记录可追溯这次写入。",
    ),
    ("ErrorRecoveryMiddleware", "wrap_tool_call"): (
        "工具调用出现可恢复的异常。", "自动重试可恢复的工具错误。", "重试仍失败时返回错误和修正建议。",
    ),
    ("ReadSentinelMiddleware", "wrap_tool_call"): (
        "同样的读取参数再次返回相同内容。", "提醒模型已读过相同内容。", "减少无效的反复读取。",
    ),
    ("FilesystemPathGuardMiddleware", "wrap_tool_call"): (
        "文件工具请求写入某个路径。", "修正可规范的写入路径并拦截越界路径。", "写入只发生在允许的目录范围内。",
    ),
    ("EncodingGuardMiddleware", "wrap_tool_call"): (
        "文件工具读取文本，或完成写入后需要校验。", "尝试兼容解码，检查写入编码与内容截断。",
        "读取遇到编码问题时尝试 UTF-8-SIG。写入校验失败时尝试删除损坏文件，并返回错误。",
    ),
    ("FileStateTrackerMiddleware", "wrap_tool_call"): (
        "工具尝试按原文片段编辑文件。", "验证待替换的文字仍能在文件中匹配。", "匹配失败时反馈当前文件状态。",
    ),
    ("FileWriteSerializeMiddleware", "wrap_tool_call"): (
        "多个写操作同时指向同一个文件。", "让同一个文件的写操作依次执行。", "减少同时写入造成的内容覆盖。",
    ),
    ("WriteResultInspectorMiddleware", "wrap_tool_call"): (
        "写工具返回错误状态。", "把写入失败交给外层的错误恢复规则。", "失败能进入重试或清晰的错误处理。",
    ),
    ("ArtifactSnapshotMiddleware", "wrap_tool_call"): (
        "快照功能已启用，且写工具成功保存产物。", "保存这次产物修订的快照。", "后续可以追溯和查看当时的内容。",
    ),
}

_CONDITIONS = {
    "FilesystemPathGuardMiddleware": "写入路径需要规范时修正路径，超出允许范围时拒绝写入。",
    "ErrorRecoveryMiddleware": "工具调用抛出可恢复异常时重试；仍失败时返回错误和修正建议。",
    "FileWriteSerializeMiddleware": "写工具并发操作同一文件时依次执行，避免写入互相覆盖。",
    "ReadSentinelMiddleware": "按相同参数重复读到相同内容时，给返回结果加上重复读取提示。",
    "EncodingGuardMiddleware": "读取时尝试兼容解码。写入后检查编码和截断，校验失败时清理损坏文件并报错。",
    "FileStateTrackerMiddleware": "编辑前检查原文能否匹配；匹配失败时返回文件状态提示。",
    "WriteResultInspectorMiddleware": "写工具以错误状态返回时，转为异常，由外层恢复规则处理。",
    "ArtifactSnapshotMiddleware": "已接入快照回调且写工具成功落盘时，记录该次产物修订。",
    "TraceMiddleware": "已提供运行编号和记录器时，记录模型与工具调用过程。",
    "StageGateMiddleware": "请求写入所属职责以外的产物时，拒绝该次写入。",
    "TextCompletionMiddleware": "正文拟交付时检查保存和审稿记录，条件不足时要求补齐。",
    "ToolCallRecoveryMiddleware": "正文模型把工具调用写成普通文字时，最多提示两次改用真实工具。",
    "RevisionLimitMiddleware": "审查委托超过本套修订上限时，停止继续委托。",
    "ContextAssemblerMiddleware": "清单声明输入文件时，读取相应资料并组装到当前任务。",
    "ArtifactValidationMiddleware": "清单声明目标产物时，核对所需交付并反馈缺项。",
}
