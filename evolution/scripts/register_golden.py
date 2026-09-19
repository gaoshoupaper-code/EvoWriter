"""评测 golden 数据集注册脚本（REQ-20260919-172934 / FR-001）。

把 evalset/golden/ 磁盘 case 注册进 dataset_meta（修复现状 0 行注册问题），
并锁定 golden_revision 指纹。幂等：可重复执行。

用法（evolution 目录下）：
  python -m scripts.register_golden [--dry-run]

校验（FR-001 失败语义）：缺 title/status front-matter 或正文过短的 case 视为
无效，拒绝注册并报告。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 允许作为脚本或模块运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.common import evalset  # noqa: E402
from app.dataset import repo as dataset_repo  # noqa: E402
from app.dataset import revision  # noqa: E402

# 有效 case 的最低正文长度（字符，含 front-matter）
_MIN_DEMAND_CHARS = 500

_TITLE_RE = re.compile(r"^-\s*title:\s*(.+?)\s*$", re.MULTILINE)
_STATUS_RE = re.compile(r"^-\s*status:\s*(\w+)", re.MULTILINE)


def validate_case(case_id: str) -> tuple[bool, str]:
    """校验单个 case：front-matter 必填字段 + 正文长度。返回 (ok, reason)。"""
    try:
        demand_md = evalset.load_case_demand(case_id, layer="golden")
    except FileNotFoundError as exc:
        return False, str(exc)
    if not _TITLE_RE.search(demand_md[:400]):
        return False, "front-matter 缺 title 字段"
    status = _STATUS_RE.search(demand_md[:400])
    if not status:
        return False, "front-matter 缺 status 字段"
    if status.group(1) != "confirmed":
        return False, f"status={status.group(1)}（要求 confirmed）"
    if len(demand_md) < _MIN_DEMAND_CHARS:
        return False, f"正文过短（{len(demand_md)} < {_MIN_DEMAND_CHARS} 字符）"
    return True, ""


def register(dry_run: bool = False) -> int:
    cases = evalset.list_cases(layer="golden")
    if not cases:
        print("golden 目录为空，无可注册 case")
        return 1

    invalid: list[tuple[str, str]] = []
    valid: list[str] = []
    for case_id in cases:
        ok, reason = validate_case(case_id)
        if ok:
            valid.append(case_id)
        else:
            invalid.append((case_id, reason))

    print(f"golden 扫描：{len(cases)} case，有效 {len(valid)}，无效 {len(invalid)}")
    for case_id, reason in invalid:
        print(f"  [无效] {case_id}: {reason}")
    if invalid:
        print("存在无效 case，拒绝批量注册（修复后重跑）")
        return 1

    if dry_run:
        rev = revision.compute_golden_revision()
        print(f"[dry-run] 将注册 {len(valid)} case 并锁定 golden_revision={rev}")
        return 0

    from app.core import db as _db  # noqa: F401  # 确保库已初始化（import 副作用）

    for case_id in valid:
        dataset_repo.register_case(
            case_id=case_id,
            layer="golden",
            created_by="maintainer",
        )
    rev = revision.compute_golden_revision()
    # update_demand_revision 实现为全 golden 行统一刷新，case_id 仅作签名兼容
    dataset_repo.update_demand_revision(valid[0], rev)
    print(f"已注册 {len(valid)} case，golden_revision 锁定为 {rev}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="注册 golden 评测集并锁定指纹")
    parser.add_argument("--dry-run", action="store_true", help="只校验不落库")
    raise SystemExit(register(dry_run=parser.parse_args().dry_run))
