"""save_artifact 工具 — 语义化落盘 + 校验编辑（REQ-20261018-115508 FR-003/004）。

路径收权（FR-003）：kind+卷号/章号 → 路径的唯一映射在 contracts 层，agent
不见路径；工具 schema 不含任何路径参数（AC-005）。

校验编辑四道保险（DEC-007，ZCode Edit 同款机制——打歪的补丁应用不上去，
只会报错重试，fail-closed）：
  1. 先读后改：mode=edit 与对已存在文件的 mode=full 都要求本装配周期内
     先 read_artifact 过该产物（读取登记在 ctx.artifact_read_registry）。
  2. 精确+多级归一匹配：先逐字精确；失败后按行去尾空白再试；再失败按
     引号归一（中英引号视为相同）再试。
  3. 锚点唯一性：匹配多于一处 → 报错要求带更多上下文，不落盘。
  4. 失败不落盘：任何校验不过，原文件字节不变，返回错误供重试。

重试上限退化兜底（DEC-007）：同一产物编辑连续失败 3 次 → 拒绝继续 edit，
强制引导 mode=full 提交完整内容（工具无法凭空补全全文，退化以「堵 edit、
导流 full」实现）；成功落盘后计数清零。

每次「锚点→替换」经既有 TraceMiddleware 进 trace（L4 审计，DEC-008）。
落盘成功后经 ctx.trace_recorder 冻结 ArtifactRevision——进化侧产物库的唯一
摄入源（REQ-20261019-203500 FR-001）：内容在手直记，不重读文件；取证失败
静默降级留痕，绝不影响落盘（DEC-004）。
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from contracts.writing_artifacts import (
    KIND_CHAPTER,
    KIND_OUTLINE,
    KIND_STYLE,
    KIND_TEXT,
    KIND_THEME,
    KIND_VOLUME,
    STAGE_VOLUME,
    artifact_relpath,
    resolve_confirmed_stage,
)

logger = logging.getLogger(__name__)

# 编辑重试上限：同一产物连续失败到该次数后封死 edit、强制 full（DEC-007）
_EDIT_FAILURE_CAP = 3
VOLUME_PAYOFF_GUARD_VERSION = 1
_MISSING_VOLUME_GUARD = object()

SAVE_ARTIFACT_TOOL_DESCRIPTION = """按产物种类落盘（不接触文件路径）。每个点被作者确认后立即落盘。

kind 取值：theme / outline / style / volume_outline(+volume) / chapter_outline(+chapter) / chapter_text(+chapter)。
正文文件名由章号决定（不带章名——章名写进文件头标题行「# 第NNN章 · <章名>」）。

两种模式：
- mode="full"：content = 该产物的完整新内容（新建文件、推翻重做大改用）。
- mode="edit"（默认，推荐）：anchor + replacement——anchor 是文件里的原文片段
  （须逐字一致且在文件中唯一，建议带上标题行等上下文）；replacement 是替换后
  的文本（新增一张卡 = anchor 所在区块末尾 + 新卡片内容；状态行变更同理）。
  编辑前必须先 read_artifact 读过该产物；锚点不唯一/找不到会报错且文件不变，
  带更多上下文重试；连续失败 3 次后改用 mode="full" 提交完整内容。

仅卷纲支持 draft=true：读取并编辑修订草稿，须先 read_artifact(draft=true)。
卷纲正式提交会核验完整候选；检查未通过时保留草稿并说明原因，不能宣称定稿。

落盘内容遵守文件头两行约定：标题行 + 状态日期行；约束级/参考级分区。"""


class SaveArtifactInput(BaseModel):
    kind: str = Field(description="产物种类：theme/outline/style/volume_outline/chapter_outline/chapter_text")
    volume: int | None = Field(default=None, ge=1, description="卷号（volume_outline 必填）")
    chapter: int | None = Field(default=None, ge=1, description="章号（chapter_outline/chapter_text 必填）")
    mode: str = Field(default="edit", description='"edit"（默认，锚点校验编辑）| "full"（整文件替换）')
    anchor: str | None = Field(default=None, description="edit 模式：要替换的原文片段（须唯一）")
    replacement: str | None = Field(default=None, description="edit 模式：替换后的文本")
    content: str | None = Field(default=None, description="full 模式：完整新内容")
    draft: bool = Field(default=False, description="仅卷纲：以修订草稿为编辑基线")


def _quote_norm(s: str) -> str:
    return (s.replace("\u201c", '"').replace("\u201d", '"')
             .replace("\u2018", "'").replace("\u2019", "'"))


def _find_line_span(text: str, anchor: str, *, quote: bool) -> list[tuple[int, int]] | None:
    """按行归一匹配：返回 [行起止偏移] 候选（行级整段替换）；无匹配返回 None。

    归一规则：每行去尾部空白（含换行）后比较；quote=True 时引号归一。
    """
    file_lines = text.splitlines(keepends=True)
    anchor_lines = anchor.splitlines()
    norm = _quote_norm if quote else (lambda s: s)
    a = [norm(ln.rstrip()) for ln in anchor_lines]
    if not a or any(not ln and i in (0, len(a) - 1) for i, ln in enumerate(a)):
        return None
    f = [norm(ln.rstrip()) for ln in file_lines]
    spans: list[tuple[int, int]] = []
    n, m = len(f), len(a)
    for i in range(n - m + 1):
        if f[i:i + m] == a:
            start = sum(len(ln) for ln in file_lines[:i])
            end = start + sum(len(ln) for ln in file_lines[i:i + m])
            spans.append((start, end))
    return spans or None


def find_edit_match(content: str, anchor: str) -> tuple[int, int] | None | str:
    """锚点匹配三档：精确 → 行归一 → 行归一+引号归一。

    Returns:
        (start, end)  唯一匹配的原文偏移区间
        None          找不到
        "ambiguous"   多于一处
    """
    count = content.count(anchor)
    if count == 1:
        i = content.index(anchor)
        return (i, i + len(anchor))
    if count > 1:
        return "ambiguous"
    for quote in (False, True):
        spans = _find_line_span(content, anchor, quote=quote)
        if spans is None:
            continue
        if len(spans) == 1:
            return spans[0]
        if len(spans) > 1:
            return "ambiguous"
    return None


def apply_edit(content: str, anchor: str, replacement: str) -> tuple[str, int] | str:
    """应用一次校验编辑。成功返回 (新内容, 替换处数)；失败返回错误说明。"""
    match = find_edit_match(content, anchor)
    if match == "ambiguous":
        return "锚点在文件中不唯一：带上标题行等更多上下文重试，或改用 mode=full。"
    if match is None:
        return "锚点在文件中找不到（须与原文逐字一致）：重新 read_artifact 后按当前内容重试。"
    start, end = match
    return content[:start] + replacement + content[end:], 1


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import uuid as _uuid

    tmp = path.with_suffix(f"{path.suffix}.{_uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _describe(kind: str, volume: int | None, chapter: int | None) -> str:
    if volume:
        return f"{kind}（第 {volume} 卷）"
    if chapter:
        return f"{kind}（第 {chapter} 章）"
    return kind


def save_artifact(*args, **kwargs) -> str:  # pragma: no cover - 由 build 闭包实现
    raise NotImplementedError("由 build(abc) 闭包实现（需要 workspace 与登记容器）")


def _guard_volume_write(guard, *, volume: int, base_text: str,
                        candidate_text: str, source: str, draft: bool = False,
                        edit_scope: dict | None = None, capture=None) -> dict | None:
    if guard is None:  # 显式无约束，兼容未启用定义的工作区及低层工具调用。
        return None
    if guard is _MISSING_VOLUME_GUARD:
        return {"status": "blocked", "reason": "运行端缺少卷纲检查能力，请更新后重试。"}
    try:
        result = guard.commit(
            volume=volume, base_text=base_text, candidate_text=candidate_text,
            source=source, draft=draft, edit_scope=edit_scope,
        )
        if not isinstance(result, dict) or result.get("status") not in {"committed", "draft", "blocked", "retryable"}:
            raise ValueError("卷纲检查结果格式不合法")
        if result["status"] == "committed" and (
            not isinstance(result.get("path"), str) or not isinstance(result.get("content"), str)
        ):
            raise ValueError("卷纲提交结果缺少实际内容")
    except Exception:  # noqa: BLE001 — 检查异常不得退回无约束写入。
        logger.exception("卷纲检查失败，保留原稿")
        return {"status": "blocked", "reason": "卷纲检查失败，原稿未覆盖；请稍后重试。"}
    if capture and isinstance(result.get("path"), str) and isinstance(result.get("content"), str):
        try:
            capture(result["path"], result["content"])
        except Exception:  # noqa: BLE001 — 取证失败不改变已完成的业务提交。
            logger.warning("卷纲写入快照失败，已完成的提交不重放", exc_info=True)
    return result


def _volume_write_message(result: dict, volume: int, *, rewrite: bool = False) -> str:
    if result.get("status") == "committed":
        action = f"第 {volume} 卷节奏表已回写" if rewrite else f"已落盘 volume_outline（第 {volume} 卷）"
        return f"{action}（完整卷纲检查通过；自动修正 {result.get('repair_attempts', 0)} 轮）。"
    location = f"草稿：{result['path']}。" if result.get("path") else "原稿未覆盖。"
    guidance = ""
    if result.get("path"):
        draft = result["path"].endswith("-修订草稿.md")
        guidance = f"继续修订先 read_artifact(kind=volume_outline, volume={volume}, draft={str(draft).lower()})，再按同一 draft 值编辑。"
    if result.get("status") == "retryable":
        guidance += "检查暂时失败，可稍后重试本次提交；沿用原候选与已用修复额度。"
    return f"[卷纲未定稿] {location}{result.get('reason') or '检查未通过'}。{guidance}"


def build_save_artifact_tool(
    workspace_root: Path, registry: dict, failures: dict,
    *, recorder=None, trace_id: str | None = None,
    agent_name: str = "author-subagent",
    volume_guard=None, restrict_volume_edits: bool = False,
) -> StructuredTool:
    """构建落盘工具。

    Args:
        workspace_root: 工作区根。
        registry:       读取登记（read_artifact 写入，先读后改保险）。
        failures:       编辑失败计数（重试上限退化兜底）。
        recorder:       TraceRecorder（取证用；None = 无追踪场景，跳过取证）。
        trace_id:       当前追踪会话标识（None = 无追踪场景）。
        agent_name:     取证事件的 agent 名（用 trace 埋点名，如 author-subagent）。
    """

    def _capture_revision(rel: str, text: str) -> None:
        """落盘成功后冻结 ArtifactRevision（进化侧产物库摄入源）。

        内容在手直记，不重读文件——重读会与同文件并发写竞态（平台取证层
        前科 12494f65）。取证失败静默降级（DEC-004）：留 capture_degraded
        痕迹，绝不影响落盘结果与工具返回。

        取证身份内容寻址（R1/P0）：tool_call_id 由内容哈希派生。recorder 的
        call_paths 守卫按 (tool_call_id, 路径) 判「同调用不同内容 = 冲突」——
        恒定 id 会让同一产物的后续修订全部被误判冲突静默丢弃；per-call 随机
        id 又会让同内容重落失去幂等去重。内容寻址两头都成立：同内容 → 同 id
        → 去重返回既有修订；内容变 → id 变 → 新修订且不触冲突守卫。
        """
        if recorder is None or not trace_id:
            return
        capture_id = "save_artifact:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        try:
            recorder.record_artifact_revision(
                trace_id, agent_name,
                file_path=rel, content=text, tool_name="save_artifact",
                tool_call_id=capture_id,
            )
        except Exception as exc:  # noqa: BLE001 — 取证失败不得影响写作（DEC-004）
            marker = getattr(recorder, "_mark_capture_degraded", None)
            if callable(marker):
                try:
                    marker(trace_id, f"save_artifact_capture_failed:{rel}:{type(exc).__name__}")
                except Exception:
                    pass
            logger.warning("save_artifact 产物取证失败（已降级）：%s：%s", rel, exc)

    def _save(kind: str, volume: int | None = None, chapter: int | None = None,
              mode: str = "edit",
              anchor: str | None = None, replacement: str | None = None,
              content: str | None = None, draft: bool = False) -> str:
        if draft and kind != KIND_VOLUME:
            return "[save_artifact 失败] draft 仅卷纲可用。"
        try:
            rel = artifact_relpath(kind, volume=volume, chapter=chapter)
            if draft:
                from contracts.writing_artifacts import volume_draft_relpath

                rel = volume_draft_relpath(volume)
        except ValueError as exc:
            return f"[save_artifact 失败] {exc}"
        if mode not in ("edit", "full"):
            return "[save_artifact 失败] mode 只能是 edit 或 full。"
        path = workspace_root / rel
        exists = path.is_file()
        current = ""
        if exists:
            try:
                current = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                return f"[save_artifact 失败] 读取现状失败：{type(exc).__name__}"

        # ── mode=edit：产物不存在的判定先于读档要求（读不存在的产物只会得到
        #    「不存在」指引，不应拿读档拦路）──
        if mode == "edit" and not exists:
            return "[save_artifact 失败] 产物尚不存在，新建用 mode=full。"

        # ── 保险 1：先读后改（edit 改已存在文件；full 改已存在文件同样要）──
        if exists and not registry.get(rel):
            return (
                "[save_artifact 拒绝] 先调 read_artifact 读取该产物，再落盘——"
                "锚点/整文件替换都必须基于刚读到的当前内容。"
            )
        read_entry = registry.get(rel)
        if exists and kind == KIND_VOLUME and isinstance(read_entry, dict):
            fingerprint = hashlib.sha256(current.encode("utf-8")).hexdigest()
            if read_entry.get("content_hash") != fingerprint:
                return "[save_artifact 拒绝] 卷纲自读取后已变化，请重新 read_artifact 后合并修改。"

        if mode == "full":
            if content is None or not content.strip():
                return "[save_artifact 失败] mode=full 需要 content（完整新内容）。"
            if kind == KIND_VOLUME:
                guarded = _guard_volume_write(
                    volume_guard, volume=volume, base_text=current,
                    candidate_text=content, source="full", draft=draft,
                    capture=_capture_revision,
                )
                if guarded is not None:
                    if guarded.get("status") == "committed":
                        failures.pop(rel, None)
                    return _volume_write_message(guarded, volume)
            try:
                _atomic_write(path, content)
            except OSError as exc:
                return f"[save_artifact 失败] 写入失败：{type(exc).__name__}"
            _capture_revision(rel, content)
            failures.pop(rel, None)
            lines = len(content.splitlines())
            return f"已落盘 {_describe(kind, volume, chapter)}（全量 {lines} 行）。"

        # ── mode=edit ──
        if failures.get(rel, 0) >= _EDIT_FAILURE_CAP:
            return (
                "[save_artifact 拒绝] 该产物编辑已连续失败 "
                f"{failures[rel]} 次：停止重试锚点，改用 mode=full 提交完整内容"
                "（read 后整文件重写，随后失败计数自动清零）。"
            )
        if not anchor or replacement is None:
            return "[save_artifact 失败] mode=edit 需要 anchor 与 replacement。"
        result = apply_edit(current, anchor, replacement)
        if isinstance(result, str):
            failures[rel] = failures.get(rel, 0) + 1
            return f"[save_artifact 失败·文件未变] {result}"
        new_text, hits = result
        if kind == KIND_VOLUME:
            edit_scope = None
            if restrict_volume_edits:
                start, end = find_edit_match(current, anchor)
                edit_scope = {"prefix": current[:start], "suffix": current[end:]}
            guarded = _guard_volume_write(
                volume_guard, volume=volume, base_text=current,
                candidate_text=new_text, source="edit", draft=draft,
                edit_scope=edit_scope, capture=_capture_revision,
            )
            if guarded is not None:
                if guarded.get("status") == "committed":
                    failures.pop(rel, None)
                return _volume_write_message(guarded, volume)
        try:
            _atomic_write(path, new_text)
        except OSError as exc:
            return f"[save_artifact 失败] 写入失败：{type(exc).__name__}"
        _capture_revision(rel, new_text)
        failures.pop(rel, None)
        return f"已落盘 {_describe(kind, volume, chapter)}（校验编辑：替换 {hits} 处，仅目标区段变更）。"

    return StructuredTool.from_function(
        name="save_artifact",
        description=SAVE_ARTIFACT_TOOL_DESCRIPTION,
        func=_save,
        args_schema=SaveArtifactInput,
        infer_schema=False,
    )


def build(abc):
    """架构清单挂载钩子：与 read_artifact 共享读取登记；失败计数挂 ctx。

    取证接线（REQ-20261019-203500）：recorder/trace_id 来自 RuntimeContext，
    agent 名用 trace 埋点名（与 TraceMiddleware 同源，如 author-subagent）。
    """
    registry = getattr(abc.ctx, "artifact_read_registry", None)
    if registry is None:
        registry = {}
        try:
            abc.ctx.artifact_read_registry = registry
        except Exception:  # noqa: BLE001 — ctx 不可写时自持（降级同 read 侧）
            pass
    failures = getattr(abc.ctx, "artifact_edit_failures", None)
    if failures is None:
        failures = {}
        try:
            abc.ctx.artifact_edit_failures = failures
        except Exception:  # noqa: BLE001
            pass
    ctx = getattr(abc, "ctx", None)
    return build_save_artifact_tool(
        abc.workspace_path, registry, failures,
        recorder=getattr(ctx, "trace_recorder", None),
        trace_id=getattr(ctx, "trace_id", None),
        agent_name=getattr(abc, "trace_name", None) or "author-subagent",
        volume_guard=getattr(ctx, "volume_write_guard", _MISSING_VOLUME_GUARD),
        restrict_volume_edits=resolve_confirmed_stage(abc.workspace_path) != STAGE_VOLUME,
    )


__all__ = [
    "SAVE_ARTIFACT_TOOL_DESCRIPTION",
    "SaveArtifactInput",
    "VOLUME_PAYOFF_GUARD_VERSION",
    "apply_edit",
    "build",
    "build_save_artifact_tool",
    "find_edit_match",
]
