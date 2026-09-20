"""数据集管理 API（数据闭环设计 A3）。

端点：
  GET  /api/dataset/cases               列出 case（按 layer 过滤，带元数据）
  GET  /api/dataset/cases/{case_id}     单 case 内容（demand.md + reference.md）
  GET  /api/dataset/golden-revision     当前 golden 锁定的 revision
  POST /api/dataset/golden/cases        受控新增 golden case（REQ-20260920-104714/FR-004）

golden 变更原则（重构 2026-07-10 → DEC-008 受控调整）：
  golden 以 git 仓库为权威源。原「运行时只读、禁止一切写入」调整为：
  运行时开放唯一受控新增通道（写文件 + dataset_meta 登记 + git 提交 + 重锁
  revision），审计链仍落 git；删除/编辑/启停不做界面入口，走 git 手动。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.common import evalset
from app.dataset import repo as dataset_repo
from app.dataset import revision

logger = logging.getLogger("evolution.dataset.api")

router = APIRouter(prefix="/dataset", tags=["dataset"])


# ── 列表 ────────────────────────────────────────────────────


@router.get("/cases")
def list_cases(
    layer: str | None = Query(None, description="golden|growing|空=全部"),
) -> dict[str, Any]:
    """列出数据集 case（文件系统 + dataset_meta 元数据合并）。

    以文件系统为准（demand.md 存在才算 case），元数据从 dataset_meta 补充。
    """
    # 文件系统 case 列表（带 title）
    try:
        fs_cases = evalset.list_cases_with_title(layer=layer)
    except Exception:
        logger.error("evalset 文件系统扫描失败", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="数据集目录扫描失败，请检查 evalset 目录状态",
        )

    # 元数据索引（表缺失/查询失败时降级为空，不阻塞文件系统 case 列表）
    try:
        meta_rows = dataset_repo.list_by_layer(layer=layer)
        meta_map = {r["case_id"]: r for r in meta_rows}
    except Exception:
        logger.warning("dataset_meta 查询失败，降级为无元数据", exc_info=True)
        meta_map = {}

    cases = []
    for c in fs_cases:
        case_id = c["case_id"]
        meta = meta_map.get(case_id, {})
        cases.append(
            {
                "case_id": case_id,
                "title": c["title"],
                "layer": c["layer"],
                "source_trace_id": meta.get("source_trace_id"),
                "demand_revision": meta.get("demand_revision"),
                "promoted_at": meta.get("promoted_at"),
                "created_by": meta.get("created_by", "manual"),
                "has_reference": evalset.reference_path(case_id, layer=c["layer"]).exists(),
            }
        )
    return {"cases": cases, "total": len(cases)}


# ── 单 case 内容 ─────────────────────────────────────────────


@router.get("/cases/{case_id}")
def get_case_content(
    case_id: str,
    layer: str | None = Query(None, description="golden|growing|空=自动推导"),
) -> dict[str, Any]:
    """读取单个 case 的 demand.md + reference.md 内容。

    供前端详情侧滑面板展示（列表接口只返回元数据，不返回文件内容）。
    """
    ly = layer or evalset.resolve_layer(case_id) or evalset.DEFAULT_LAYER
    if not evalset.case_exists(case_id, layer=ly):
        raise HTTPException(status_code=404, detail=f"case {case_id} 不存在")

    demand_md = evalset.load_case_demand(case_id, layer=ly)
    title = evalset.parse_title(demand_md, case_id)

    ref_path = evalset.reference_path(case_id, layer=ly)
    reference_md = ref_path.read_text(encoding="utf-8") if ref_path.exists() else None

    meta = dataset_repo.get(case_id) or {}
    return {
        "case_id": case_id,
        "title": title,
        "layer": ly,
        "demand_md": demand_md,
        "reference_md": reference_md,
        "source_trace_id": meta.get("source_trace_id"),
        "demand_revision": meta.get("demand_revision"),
        "promoted_at": meta.get("promoted_at"),
        "created_by": meta.get("created_by", "manual"),
        "status": meta.get("status", "active"),
    }


# ── golden revision ─────────────────────────────────────────


@router.get("/golden-revision")
def get_golden_revision() -> dict[str, Any]:
    """当前 golden 集锁定的 revision + case 列表。

    revision 来自 dataset_meta（锁定值）；若元数据缺失则实时计算（未锁定状态）。
    """
    # revision 计算与 DB 元数据解耦：compute 为纯文件系统（必成功），
    # DB 查询（锁定值）失败时降级——case 列表始终以文件系统为准（与 list_cases
    # 端点一致），避免手动建目录（未注册元数据）时 case_count 显示 0。
    current = revision.compute_golden_revision()
    golden_cases = evalset.list_cases(layer="golden")
    locked = None
    try:
        locked = dataset_repo.get_golden_revision()
    except Exception:
        logger.warning("dataset_meta 查询失败，golden-revision 降级", exc_info=True)

    return {
        "revision": locked or current,
        "locked": locked is not None,
        "intact": revision.verify_golden_intact(locked) if locked else True,
        "case_count": len(golden_cases),
        "cases": golden_cases,
    }


# ── 受控新增 golden case（FR-004/DEC-008）──────────────────


class GoldenCaseCreateRequest(BaseModel):
    """受控新增请求：demand.md 全文（case_id 自动编号）。"""
    demand_md: str


def _next_golden_case_id() -> str:
    """扫描 golden 目录现有编号，取最大值 +1（case-001 起）。"""
    import re

    golden_dir = evalset.layer_root("golden")
    nums = [
        int(m.group(1))
        for d in golden_dir.iterdir()
        if d.is_dir() and (m := re.fullmatch(r"case-(\d+)", d.name))
    ]
    return f"case-{(max(nums) + 1) if nums else 1:03d}"


def _git(repo_root: Path, *args: str, timeout: float = 30.0) -> str:
    """跑一条 git 命令（内联 identity，不依赖全局 git config）。失败 raise。"""
    import subprocess

    proc = subprocess.run(
        ["git", "-C", str(repo_root),
         "-c", "user.name=evolution-benchmark",
         "-c", "user.email=benchmark@evolution.local",
         *args],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


def _rollback_new_case(
    case_dir: Path, case_id: str,
    repo_root: Path | None = None, demand_path: Path | None = None,
) -> None:
    """新增失败回滚（FR-004 失败语义：零残留）。

    删文件 + 删 dataset_meta 行 + 把锁定 revision 重算回旧值（文件已删，
    compute 结果即回滚前状态）。git add 已发生时（repo_root 传入了）
    best-effort unstage——index 里残留的 staged blob 不清，之后任何一次
    commit 都会把工作区已删的幽灵文件卷进去（review P1）。
    每步 best-effort，失败记日志不掩盖原始错误。
    """
    import shutil

    try:
        shutil.rmtree(case_dir, ignore_errors=True)
        if repo_root is not None and demand_path is not None:
            try:
                _git(repo_root, "reset", "-q", "--", str(demand_path))
            except Exception:
                logger.warning("golden 新增回滚 unstage 失败 path=%s", demand_path)
        dataset_repo.delete_case(case_id)
        dataset_repo.update_demand_revision(case_id, revision.compute_golden_revision())
    except Exception:
        logger.exception("golden 新增回滚不完整 case=%s，需人工检查", case_id)


# 受控新增全程串行锁（单进程服务足够；防并发请求算出同一 case_id 后
# 后写者回滚吞掉先写者，review #7）
_golden_add_lock = threading.Lock()


@router.post("/golden/cases")
def create_golden_case(req: GoldenCaseCreateRequest) -> dict[str, Any]:
    """受控新增 golden case：写文件 → 登记 → 重锁 → git 提交 → 尝试 push。

    流程与失败语义（FR-004/AC-007）：
      1. 校验 demand_md 非空（空/全空白 → 400，零残留）
      2. 自动编号写 golden/case-XXX/demand.md + dataset_meta 登记
      3. compute 新 revision 并重锁全部 golden 行
      4. git add + commit（pathspec 限定本文件，审计链）——失败则整体回滚
         （文件+登记+旧锁+unstage），500
      5. git push 尝试（fail-soft：commit 已保审计，push 失败只返回 warning）
    新增成功后历史批次与新批次 golden_revision 不同，对比将被正确拒绝（预期）。
    """
    content = req.demand_md.strip()
    if not content:
        raise HTTPException(status_code=400, detail="demand.md 内容为空，请填写创作需求全文")

    with _golden_add_lock:
        return _create_golden_case_locked(content)


def _ensure_audit_repo(evalset_root: Path) -> Path:
    """解析 golden 审计 git 仓库根（DEC-008：变更必须落 git）。

    常规路径：evalset 在主仓库内（开发机 / 宿主跑），rev-parse 向上找到仓库根。
    自愈路径：生产容器不带 .git（dockerignore 排除），rev-parse 失败时在
    evalset 目录自身幂等初始化独立审计仓库（volume 内持久，容器重建不丢）——
    审计链仍完整，只是与主仓库解耦。
    """
    try:
        return Path(_git(evalset_root, "rev-parse", "--show-toplevel"))
    except Exception:
        logger.info("evalset 无上级 git 仓库（容器部署态），初始化独立审计仓库: %s", evalset_root)
        _git(evalset_root, "init", "-b", "main")
        _git(evalset_root, "add", "-A")
        # 空目录无可提交内容 → commit 失败是正常态，跳过基线
        try:
            _git(evalset_root, "commit", "-m", "golden audit repo baseline")
        except Exception:
            pass
        return evalset_root


def _create_golden_case_locked(content: str) -> dict[str, Any]:
    case_id = _next_golden_case_id()
    case_dir = evalset.layer_root("golden") / case_id
    demand_path = case_dir / "demand.md"

    # 审计仓库解析前置（写文件之前）：容器态自愈 init 的基线提交若发生在
    # 新文件写入之后，会把待提交的 case 一起卷进基线，随后的 pathspec
    # commit 变成 nothing-to-commit 而失败。
    try:
        repo_root = _ensure_audit_repo(evalset.evalset_root())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"golden 审计仓库不可用：{exc}")

    # 1-3. 写文件 + 登记 + 重锁（此时 git 未动，任何异常整体回滚）
    try:
        case_dir.mkdir(parents=True, exist_ok=False)
        demand_path.write_text(content, encoding="utf-8")
        dataset_repo.register_case(
            case_id=case_id, layer="golden", created_by="benchmark-ui",
        )
        new_revision = revision.compute_golden_revision()
        dataset_repo.update_demand_revision(case_id, new_revision)
    except Exception as exc:
        _rollback_new_case(case_dir, case_id)
        raise HTTPException(status_code=500, detail=f"golden 新增失败（已回滚）：{exc}")

    # 4. git 提交（pathspec 限定本文件：不卷入 index 里任何无关 staged 改动）
    try:
        _git(repo_root, "add", str(demand_path))
        _git(repo_root, "commit", "-m", f"benchmark: 受控新增 golden case {case_id}",
             "--", str(demand_path))
        commit = _git(repo_root, "rev-parse", "HEAD")
    except Exception as exc:
        _rollback_new_case(case_dir, case_id, repo_root=repo_root, demand_path=demand_path)
        raise HTTPException(status_code=500, detail=f"golden 新增 git 提交失败（已回滚）：{exc}")

    # 5. push 尝试（fail-soft：服务器凭据缺失/网络问题不回滚已生效的新增）
    warning = _try_git_push(repo_root, case_id)

    logger.info("golden 受控新增完成 case=%s revision=%s commit=%s", case_id, new_revision, commit[:8])
    return {
        "case_id": case_id,
        "golden_revision": new_revision,
        "git_commit": commit,
        "git_push_warning": warning,
    }


def _try_git_push(repo_root: Path, case_id: str) -> str | None:
    """尝试 push 新增 commit（fail-soft，FR-004：commit 已保审计，push 失败不回滚）。"""
    try:
        _git(repo_root, "push", "origin", "HEAD", timeout=20.0)
        return None
    except Exception as exc:
        logger.warning("golden case %s push 失败: %s", case_id, exc)
        return (
            f"git push 失败（新增已生效、审计已提交本地）：{exc}。"
            "请尽快手动 push 同步，否则下次部署 git pull 可能冲突。"
        )


__all__ = ["router"]
