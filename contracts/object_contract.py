"""object/*.md 物品卡契约判定器（REQ-20261004-221109 FR-001/FR-004）。

「判定器唯一实现」原则（沿用 storyline_contract / DEC-011）：契约判定放
contracts，executor 测试与 harness 运行时护栏（ObjectContractGuardMiddleware）
共用本模块，两侧不得各自实现。

卡片结构（三段式，FR-001）：
  # {物品名}
  ## 基本信息   —— 名称 / 类型 / 叙事可见性（枚举字段，必填）
  ## 详情       —— 自由段落（不校验）
  ## 轨迹       —— 四列表 | 事件 | 变化 | 归属 | 备注 |（硬格式）

校验口径：
  - 名称与文件名 stem 一致（DEC-012：文件名即唯一 ID）
  - 新建卡（磁盘无同名文件）时名称不得与现有卡撞名 → 消歧后缀
  - 变化取值限 CHANGE_TYPES 白名单；轨迹表至少一行数据
  - 轨迹事件名逐字引用 storyline.md 事件表（extract_event_names 共用）
  - storyline 反查（FR-004）：被修订删除的事件未被任何卡轨迹引用
    （事件改名视为删除——名称即锚点，改名即断链）
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from .storyline_contract import GuardViolation, extract_event_names

# ── 枚举（提示词 §物品 与护栏共用；改这里必须同步提示词）─────────

OBJECT_TYPES = ("功法", "武技", "武器", "丹药", "材料", "其他")
VISIBILITY_LEVELS = ("明线", "暗线", "伏笔")
CHANGE_TYPES = ("提及", "登场", "获得", "易主", "损坏", "修复", "遗失", "销毁", "消耗", "升级")

# ── 解析 ────────────────────────────────────────────────────

_FIELD_RE = re.compile(
    r"(?m)^(?:-\s*)?(?:\*\*)?(名称|类型|叙事可见性)(?:\*\*)?\s*[：:]\s*(.+?)\s*$"
)
_TRAJ_HEADER = re.compile(r"^\|.*事件.*\|.*变化.*\|.*归属.*\|")
_TABLE_SEPARATOR = re.compile(r"\|[\s|:-]+\|")


def _clean(s: str) -> str:
    return s.replace("**", "").strip()


@dataclass(frozen=True)
class TrajectoryRow:
    event: str
    change: str
    owner: str
    note: str = ""


@dataclass(frozen=True)
class ObjectCard:
    name: str
    type: str
    visibility: str
    trajectory: tuple[TrajectoryRow, ...]


def parse_object_card(md: str) -> ObjectCard | None:
    """解析物品卡；缺「名称」字段返回 None（不完整卡由校验端点名）。"""
    fields: dict[str, str] = {}
    for m in _FIELD_RE.finditer(md):
        fields.setdefault(_clean(m.group(1)), _clean(m.group(2)))
    if "名称" not in fields:
        return None

    trajectory: list[TrajectoryRow] = []
    in_traj = False
    for ln in md.splitlines():
        s = ln.strip()
        if s.startswith("## "):
            in_traj = "轨迹" in _clean(s)
            continue
        if not in_traj or not s.startswith("|"):
            continue
        if _TRAJ_HEADER.match(s) or _TABLE_SEPARATOR.fullmatch(s):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) >= 4:
            trajectory.append(TrajectoryRow(
                _clean(cells[0]), _clean(cells[1]), _clean(cells[2]), _clean(cells[3]),
            ))
    return ObjectCard(
        name=fields["名称"],
        type=fields.get("类型", ""),
        visibility=fields.get("叙事可见性", ""),
        trajectory=tuple(trajectory),
    )


# ── 写入校验（写前拦截判定入口）──────────────────────────────


def check_object_write(
    card_filename: str,
    card_markdown: str,
    existing_cards: dict[str, str],
    storyline_markdown: str,
) -> list[GuardViolation]:
    """校验一次物品卡写入，返回违规列表（空=放行）。

    Args:
        card_filename:      卡片文件名（如 ``青云剑.md``）
        card_markdown:      预估写入后内容（write_file=args.content；
                            edit_file=磁盘内容.replace(old, new, 1)）
        existing_cards:     磁盘现有其他卡片 {文件名: 内容}（不含本文件——
                            更新自身不算重名）
        storyline_markdown: storyline.md 磁盘当前内容（锚点来源）
    """
    violations: list[GuardViolation] = []

    card = parse_object_card(card_markdown)
    if card is None:
        violations.append(GuardViolation(
            "contract", "物品卡缺少「## 基本信息」段的「- 名称：X」字段",
        ))
        return violations

    # 结构与枚举（FR-001）
    if not card.type or card.type not in OBJECT_TYPES:
        violations.append(GuardViolation(
            "contract",
            f"物品「{card.name}」类型字段非法（「{card.type}」，须为 {'/'.join(OBJECT_TYPES)}）",
        ))
    if not card.visibility or card.visibility not in VISIBILITY_LEVELS:
        violations.append(GuardViolation(
            "contract",
            f"物品「{card.name}」叙事可见性字段非法"
            f"（「{card.visibility}」，须为 {'/'.join(VISIBILITY_LEVELS)}）",
        ))

    # 文件名即唯一 ID（DEC-012）
    stem = PurePosixPath(card_filename).stem
    if card.name != stem:
        violations.append(GuardViolation(
            "contract",
            f"物品卡「名称：{card.name}」与文件名「{stem}」不一致"
            f"（文件名即唯一标识，两者必须相同）",
        ))

    # 重名查重（仅新建；更新自身不触发）
    if card_filename not in existing_cards:
        for fn, other_md in sorted(existing_cards.items()):
            other = parse_object_card(other_md)
            if other is not None and other.name == card.name:
                violations.append(GuardViolation(
                    "contract",
                    f"物品名「{card.name}」与现有卡片 {fn} 重名"
                    f"——请在名称与文件名同步加消歧后缀（如「{card.name}（残）」）",
                ))
                break

    # 轨迹表（FR-001：至少一行；格式在 parse 阶段已保证四列）
    if not card.trajectory:
        violations.append(GuardViolation(
            "contract",
            f"物品「{card.name}」缺「## 轨迹」表或轨迹表无数据行"
            f"（建卡时事件表已定稿，至少应有一行出场/提及记录）",
        ))

    # 变化枚举（FR-001）
    for row in card.trajectory:
        if row.change not in CHANGE_TYPES:
            violations.append(GuardViolation(
                "contract",
                f"物品「{card.name}」轨迹行「{row.event}」变化取值「{row.change}」"
                f"不在枚举内（须为 {'/'.join(CHANGE_TYPES)}）",
            ))

    # 锚点存在性（FR-004 / AC-002：事件名逐字引用 storyline 事件表）
    event_names = set(extract_event_names(storyline_markdown))
    for row in card.trajectory:
        if row.event not in event_names:
            violations.append(GuardViolation(
                "contract",
                f"物品「{card.name}」轨迹行引用的事件「{row.event}」"
                f"在 storyline.md 事件表中不存在（事件名须逐字引用、创建后不改）",
            ))

    return violations


def find_dangling_refs(
    storyline_current: str,
    storyline_projected: str,
    all_cards: dict[str, str],
) -> list[GuardViolation]:
    """storyline 写入反查（FR-004 / AC-003）：本次修订删除/改名的事件
    不得被任何现有卡片轨迹引用；返回悬挂引用违规（空=放行）。

    事件改名视为删除（名称即锚点）。all_cards 为磁盘全部现有卡片
    {文件名: 内容}。
    """
    removed = (
        set(extract_event_names(storyline_current))
        - set(extract_event_names(storyline_projected))
    )
    if not removed:
        return []

    violations: list[GuardViolation] = []
    for fn, md in sorted(all_cards.items()):
        card = parse_object_card(md)
        if card is None:
            continue
        for row in card.trajectory:
            if row.event in removed:
                violations.append(GuardViolation(
                    "contract",
                    f"本次 storyline 修订删除了事件「{row.event}」，"
                    f"但物品卡 {fn} 的轨迹仍引用它——请先更新该卡轨迹（或保留该事件）",
                ))
    return violations


__all__ = [
    "CHANGE_TYPES",
    "OBJECT_TYPES",
    "ObjectCard",
    "VISIBILITY_LEVELS",
    "check_object_write",
    "find_dangling_refs",
    "parse_object_card",
]
