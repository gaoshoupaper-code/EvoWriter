"""surface 指纹提取（FR 静态三层清单，规则写死在此 + 测试固化）。

对 checkout 目录的相对路径分类（规则来源：需求 DEC-006 激活 SchemaLock）：
- prompts/** 与 skills/** 下的 .md/.txt → a_text（改它不改 State schema）
- .json/.yaml/.yml/.toml（排除根 registry.json）→ b_param（行为参数）
- .py（排除 __pycache__）→ c_code（含 state_schema 的代码）
- 其余文件忽略并记 warning（人工可见，不静默丢弃）

每项值 = 文件内容 sha256hex。产出 contracts 已定义的 SurfaceFingerprint。
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from contracts.platform import SurfaceFingerprint
from contracts.surface_types import SurfaceLayer

from app.agent.git_checkout import checkout_commit, cleanup_checkout

logger = logging.getLogger("platform.surface_scan")

# 不进指纹的目录（构建产物 / git 元数据）
_SKIPPED_DIRS = frozenset({"__pycache__", ".git"})

_TEXT_SUFFIXES = frozenset({".md", ".txt"})
_PARAM_SUFFIXES = frozenset({".json", ".yaml", ".yml", ".toml"})
_TEXT_DIR_PREFIXES = ("prompts", "skills")


def classify(rel_path: str) -> SurfaceLayer | None:
    """相对路径 → 归属层；不属任何层返回 None（忽略 + warning）。

    优先级：目录前缀规则（a_text）> 配置后缀（b_param）> .py（c_code）。
    即 skills/x/config.json 归 b_param、prompts/x.py 归 c_code——
    三层的判据是「文件形态」，目录前缀只对文本文件生效。
    """
    parts = rel_path.split("/")
    suffix = Path(rel_path).suffix.lower()
    if parts[0] in _TEXT_DIR_PREFIXES and suffix in _TEXT_SUFFIXES:
        return SurfaceLayer.A_TEXT
    if suffix in _PARAM_SUFFIXES and rel_path != "registry.json":
        return SurfaceLayer.B_PARAM
    if suffix == ".py":
        return SurfaceLayer.C_CODE
    return None


def sha256_file(path: Path) -> str:
    """文件内容 sha256hex（流式读，harness 包内有大文件也不撑内存）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_surface(root: Path) -> SurfaceFingerprint:
    """扫描 checkout 目录，产出三层文件指纹。"""
    fingerprint = SurfaceFingerprint(
        a_text={}, b_param={}, c_code={}
    )
    for dirpath, dirnames, filenames in os.walk(root):
        # 剪掉不进指纹的目录；排序保证遍历顺序确定（可重复扫描）
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIPPED_DIRS)
        for name in sorted(filenames):
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            layer = classify(rel)
            if layer is None:
                # 未分类文件不进指纹，但必须留痕——静默忽略会让指纹
                # 「看起来完整」而漏掉真实变化
                logger.warning("surface 扫描忽略未分类文件: %s", rel)
                continue
            value = sha256_file(full)
            target = getattr(fingerprint, layer.value)
            target[rel] = value
    return fingerprint


def fingerprint_commit(commit: str) -> SurfaceFingerprint:
    """checkout 指定 commit → 扫描指纹 → 清理临时目录。

    绑定自动建 manifest / production 惰性建档 / promote 都走这里。
    commit 不在 bare repo → RuntimeError（由 checkout_commit 抛出）。
    """
    checkout = checkout_commit(commit)
    try:
        return scan_surface(checkout)
    finally:
        cleanup_checkout(checkout)


__all__ = ["classify", "scan_surface", "fingerprint_commit", "sha256_file"]
