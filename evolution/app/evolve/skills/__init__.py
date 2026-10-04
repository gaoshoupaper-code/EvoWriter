"""进化驱动器的 Skills 池（可复用片段）。

组织约定：每个 skill 一个子目录。
  skills/<skill_name>/
    SKILL.md    描述 + prompt 正文（何时触发、做什么、约束）
    tools.py    该 skill 的专属工具（如有）

加载机制（轻量版，2026-10-04 随首个技能 require 落地）：
  load_skill_sections() 由 evolve_system_prompt 在 Agent 构建时调用，
  把池内全部 SKILL.md 正文拼接成注入段，追加在 system prompt 尾部
  （行为指令贴尾，近因效应）。STATIC_BLUEPRINT 本体不掺技能内容，
  蓝图 API / 前端展示不受影响。

不走 deepagents 原生 skills= 挂载的原因：原生机制靠框架 fs 工具
渐进读取技能文件，而进化 Agent 禁用了框架 fs 工具
（NoFilesystemToolsMiddleware）——require 这类全程纪律型技能
本就该常驻 prompt，全量注入是正确形态。

降级语义：池空 / 单个技能文件异常 → 跳过并记日志，不阻断 Agent 构建。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("evolution.evolve.skills")

_SKILL_FILENAME = "SKILL.md"


def load_skill_sections() -> str:
    """读取池内全部 SKILL.md，拼成 system prompt 注入段。

    扫描本包下每个子目录的 SKILL.md（按目录名排序，注入顺序确定）。
    每个技能渲染为「# 技能 · <name>」+ 描述 + 正文（frontmatter 剥离），
    技能之间空两行分隔。

    Returns:
        拼接后的注入段；池空或全部跳过 → 空串（调用方按空串不追加）。
    """
    sections: list[str] = []
    pool = Path(__file__).resolve().parent
    for entry in sorted(pool.iterdir()):
        if not (entry.is_dir() and (entry / _SKILL_FILENAME).is_file()):
            continue
        try:
            text = (entry / _SKILL_FILENAME).read_text(encoding="utf-8")
            section = _render_section(text, fallback_name=entry.name)
        except Exception:
            logger.exception("技能加载失败，跳过: %s", entry.name)
            continue
        if section:
            sections.append(section)
    return "\n\n".join(sections)


def _render_section(text: str, fallback_name: str) -> str:
    """单个 SKILL.md → 注入段：剥 frontmatter，用 name/description 做头。

    frontmatter 缺失或非法时降级：头用目录名占位，正文原样注入——
    技能内容不因元数据缺失而丢失。正文为空返回空串（跳过该技能）。
    """
    meta: dict[str, Any] = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                meta = {}
            body = parts[2]

    name = str(meta.get("name") or "").strip() or fallback_name
    description = str(meta.get("description") or "").strip()
    header = f"# 技能 · {name}"
    if description:
        header += f"\n\n{description}"

    body = body.strip()
    if not body:
        return ""
    return f"{header}\n\n{body}"


__all__ = ["load_skill_sections"]
