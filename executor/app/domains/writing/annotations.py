"""产物划词标注（REQ-20261027-213246）——持久便签＋锚点复验＋批量任务组装。

标注是用户在产物**渲染文本**上划选挂出的便签（DEC-001/007）：存储在
workspace/.writing/annotations.json（与产物文件同域，跨设备不丢）。产物源文件
是 markdown，锚点匹配前先做语法归一（去强调/删除线/行内代码/链接语法/标题/
引用/列表标记/表格线），使「渲染文本 == 归一后的源文件文本」可对齐。

状态三态（DEC-009）：
  pending   待处理（前端黄色高亮）
  resolved  已解决——已进任务包（dispatched）且引用原文被改动
  invalid   已失效——未进任务包但原文找不到（被其他改动波及）
任何复验都不删除标注；终态只由人工（重框选/删除/一键清空）处理。
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.schemas.screenplay import (
    Annotation,
    AnnotationCreateRequest,
    AnnotationReanchor,
    AnnotationUpdateRequest,
)

logger = logging.getLogger(__name__)

ANNOTATIONS_FILE = ".writing/annotations.json"

LABEL_ABSTRACT = "abstract"
LABEL_CONTRADICTION = "contradiction"
LABEL_CUSTOM = "custom"
LABELS = {LABEL_ABSTRACT, LABEL_CONTRADICTION, LABEL_CUSTOM}
LABEL_DISPLAY = {
    LABEL_ABSTRACT: "抽象设定",
    LABEL_CONTRADICTION: "前后矛盾",
    LABEL_CUSTOM: "自定义",
}
# 内置标签给 agent 的默认修改指令（DEC-008）；用户意见选填、追加在指令后（FR-005）
LABEL_INSTRUCTIONS = {
    LABEL_ABSTRACT: "排查这段的设定（语言／行为／物品等），把虚的落到实处——说清是什么、从哪来、有什么规矩，让读者看得懂",
    LABEL_CONTRADICTION: "对照总纲与前文设定消除矛盾，说明以哪边为准",
}

STATUS_PENDING = "pending"
STATUS_RESOLVED = "resolved"
STATUS_INVALID = "invalid"
TERMINAL_STATUSES = {STATUS_RESOLVED, STATUS_INVALID}

# 标注范围（DEC-006）：总纲/文风/卷纲/章纲/正文五类；主题与 legacy 不做
_ANNOTATABLE_PREFIXES = ("卷纲/", "章纲/", "正文/")
_ANNOTATABLE_FILES = {"总纲.md", "文风.md"}

# 任务消息里单条原文引用上限（超长截断；agent 定位靠 read_artifact 复核）
_QUOTE_CAP = 400

# ── 文本归一：渲染文本 ↔ markdown 源文件 对齐 ───────────────

_HEADING_RE = re.compile(r"^ {0,3}#{1,6}\s+", re.M)
_QUOTE_RE = re.compile(r"^ {0,3}>\s?", re.M)
_LIST_RE = re.compile(r"^ {0,3}(?:[-+*]|\d{1,9}[.)])\s+", re.M)
_TABLE_DELIM_RE = re.compile(r"^\s*\|?[\s:|-]*?-{3,}[\s:|-]*\|?\s*$")
_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_EMPH_RE = re.compile(r"\*\*+|__+|~~|`+")
_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """markdown 源文件 → 渲染口径文本：去语法符号、折叠空白。

    只追求「用户划选的渲染文字能在归一结果里找到」的工程对齐，不做完整
    markdown 解析——覆盖强调/删除线/行内代码/链接/标题/引用/列表/表格线。
    """
    lines = [ln for ln in text.splitlines() if not _TABLE_DELIM_RE.match(ln)]
    stripped = "\n".join(lines)
    stripped = _HEADING_RE.sub("", stripped)
    stripped = _QUOTE_RE.sub("", stripped)
    stripped = _LIST_RE.sub("", stripped)
    stripped = _LINK_RE.sub(r"\1", stripped)
    stripped = _EMPH_RE.sub("", stripped)
    stripped = stripped.replace("|", " ")
    return _WS_RE.sub(" ", stripped).strip()


def verify_anchor(file_text: str, quote: str, context_before: str = "", context_after: str = "") -> str:
    """锚点校验：quote（渲染文本）能否在产物文件内唯一定位。

    返回 unique（唯一命中，或多次命中但前后文消歧成功）/ ambiguous /
    not_found。前后文消歧：before+quote+after 拼接后在归一文本中唯一命中。
    """
    n_text = normalize_text(file_text)
    n_quote = normalize_text(quote)
    if not n_quote:
        return "not_found"
    count = n_text.count(n_quote)
    if count == 0:
        return "not_found"
    if count == 1:
        return "unique"
    if not (context_before.strip() or context_after.strip()):
        return "ambiguous"
    n_all = normalize_text(f"{context_before}{quote}{context_after}")
    return "unique" if n_all and n_all in n_text else "ambiguous"


def annotation_rel_allowed(rel: str) -> bool:
    return rel in _ANNOTATABLE_FILES or rel.startswith(_ANNOTATABLE_PREFIXES)


def _read_artifact(workspace: Path, rel: str) -> str | None:
    try:
        return (workspace / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# ── 存取（json 文件，原子写）────────────────────────────────


def load_annotations(workspace: Path) -> list[Annotation]:
    path = workspace / ANNOTATIONS_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [Annotation(**item) for item in raw.get("annotations", [])]


def save_annotations(workspace: Path, annotations: list[Annotation]) -> None:
    path = workspace / ANNOTATIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "annotations": [a.model_dump() for a in annotations]}
    tmp = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── 变更操作（ValueError 携带可读中文，端点转 422）──────────


def _locate(annotations: list[Annotation], annotation_id: str) -> int:
    for idx, ann in enumerate(annotations):
        if ann.id == annotation_id:
            return idx
    return -1


def create_annotation(workspace: Path, req: AnnotationCreateRequest) -> Annotation:
    if not annotation_rel_allowed(req.artifact_rel):
        raise ValueError(f"该产物不支持标注（范围：总纲/文风/卷纲/章纲/正文；主题与旧工作区除外）：{req.artifact_rel}")
    if req.label not in LABELS:
        raise ValueError(f"label 只能是 {'/'.join(sorted(LABELS))}")
    if req.label == LABEL_CUSTOM and not (req.custom_label or "").strip():
        raise ValueError("自定义标签必须填写标签名")
    file_text = _read_artifact(workspace, req.artifact_rel)
    if file_text is None:
        raise ValueError(f"产物文件不存在：{req.artifact_rel}")
    verdict = verify_anchor(file_text, req.quote, req.context_before, req.context_after)
    if verdict == "not_found":
        raise ValueError("在产物里找不到这段原文，请重新划选")
    if verdict == "ambiguous":
        raise ValueError("这段文字在产物里出现多次，请扩大划选范围（多带前后文）")
    now = _now()
    ann = Annotation(
        id=f"a-{uuid.uuid4().hex[:10]}",
        artifact_rel=req.artifact_rel,
        label=req.label,
        custom_label=(req.custom_label or "").strip() or None,
        note=(req.note or "").strip() or None,
        quote=req.quote,
        context_before=req.context_before,
        context_after=req.context_after,
        status=STATUS_PENDING,
        created_at=now,
        updated_at=now,
    )
    annotations = load_annotations(workspace)
    annotations.append(ann)
    save_annotations(workspace, annotations)
    return ann


def update_annotation(
    workspace: Path,
    annotation_id: str,
    req: AnnotationUpdateRequest,
) -> Annotation:
    annotations = load_annotations(workspace)
    idx = _locate(annotations, annotation_id)
    if idx < 0:
        raise LookupError("标注不存在")
    ann = annotations[idx]
    if req.note is not None:
        ann.note = req.note.strip() or None
    if req.custom_label is not None:
        ann.custom_label = req.custom_label.strip() or None
    if req.reanchor is not None:
        ra: AnnotationReanchor = req.reanchor
        file_text = _read_artifact(workspace, ann.artifact_rel)
        if file_text is None:
            raise ValueError(f"产物文件不存在：{ann.artifact_rel}")
        verdict = verify_anchor(file_text, ra.quote, ra.context_before, ra.context_after)
        if verdict == "not_found":
            raise ValueError("在产物里找不到这段原文，请重新划选")
        if verdict == "ambiguous":
            raise ValueError("这段文字在产物里出现多次，请扩大划选范围（多带前后文）")
        ann.quote = ra.quote
        ann.context_before = ra.context_before
        ann.context_after = ra.context_after
        ann.status = STATUS_PENDING
        ann.dispatched = False
    ann.updated_at = _now()
    annotations[idx] = ann
    save_annotations(workspace, annotations)
    return ann


def delete_annotation(workspace: Path, annotation_id: str) -> bool:
    annotations = load_annotations(workspace)
    idx = _locate(annotations, annotation_id)
    if idx < 0:
        return False
    annotations.pop(idx)
    save_annotations(workspace, annotations)
    return True


def clear_annotations(workspace: Path, statuses: list[str]) -> int:
    """一键清空指定状态的标注（FR-002）；待处理不在清空语义内。"""
    targets = {s for s in statuses if s in TERMINAL_STATUSES}
    annotations = load_annotations(workspace)
    kept = [a for a in annotations if a.status not in targets]
    removed = len(annotations) - len(kept)
    if removed:
        save_annotations(workspace, kept)
    return removed


# ── 复验与派发 ─────────────────────────────────────────────


def reverify_annotations(workspace: Path) -> int:
    """产物变更后的锚点复验（FR-002/DEC-009），返回状态发生变化的条数。

    pending 且原文找不到：dispatched → resolved（本条被 agent 改，DEC-003）；
    未派发 → invalid（被其他改动波及）。原文仍在/文件仍在：保持不动。
    """
    annotations = load_annotations(workspace)
    changed = 0
    for ann in annotations:
        if ann.status != STATUS_PENDING:
            continue
        file_text = _read_artifact(workspace, ann.artifact_rel)
        if file_text is not None and verify_anchor(file_text, ann.quote, ann.context_before, ann.context_after) != "not_found":
            continue
        ann.status = STATUS_RESOLVED if ann.dispatched else STATUS_INVALID
        ann.updated_at = _now()
        changed += 1
    if changed:
        save_annotations(workspace, annotations)
    return changed


def _quote_for_message(quote: str) -> str:
    return quote if len(quote) <= _QUOTE_CAP else quote[:_QUOTE_CAP] + "…（超长截断）"


def _label_line(ann: Annotation) -> str:
    if ann.label == LABEL_CUSTOM:
        return f"自定义·{ann.custom_label or ''}".rstrip("·")
    return LABEL_DISPLAY.get(ann.label, ann.label)


def _instruction_line(ann: Annotation) -> str:
    if ann.label in LABEL_INSTRUCTIONS:
        return LABEL_INSTRUCTIONS[ann.label]
    return "以作者意见为准（见下方意见）"


def dispatch_annotations(workspace: Path) -> tuple[str, list[str], int]:
    """把全部待处理标注打包成一次任务（FR-004/005）。

    发出前先复验：已失效的转 invalid 并剔除出包（AC-006）；其余标记
    dispatched。返回 (任务消息文本, 标注 id 列表, 本次剔除数)。没有可发
    条目时 ValueError。
    """
    invalidated = reverify_annotations(workspace)
    all_annotations = load_annotations(workspace)
    pending = [a for a in all_annotations if a.status == STATUS_PENDING]
    if not pending:
        raise ValueError("当前没有待处理的标注，先在产物页划选文字加标注")
    pending.sort(key=lambda a: (a.created_at, a.id))
    blocks: list[str] = []
    ids: list[str] = []
    now = _now()
    for seq, ann in enumerate(pending, start=1):
        ann.dispatched = True
        ann.updated_at = now
        ids.append(ann.id)
        lines = [
            f"【{seq}｜{ann.artifact_rel}｜{_label_line(ann)}】",
            f"原文：{_quote_for_message(ann.quote)}",
            f"指令：{_instruction_line(ann)}",
        ]
        if ann.note:
            lines.append(f"意见：{ann.note}")
        blocks.append("\n".join(lines))
    save_annotations(workspace, all_annotations)
    message = (
        f"【标注修订任务】共 {len(blocks)} 条，请按编号逐条定点修改对应产物；"
        "改完逐条回报结果，哪条改不了就保持原样并说明原因。\n\n"
        + "\n\n".join(blocks)
    )
    return message, ids, invalidated
