"""ReadCacheMiddleware — 读哨兵（原缓存机制已整砍，进化点 #3 落地版）。

历史与决策：
  原实现按文件路径做内容缓存（TTL 300s / 上限 50 条），意图是省重复读的
  磁盘 IO 与 token。实际暴露两整类风险，收益却近乎为零（命中返回的内容
  与磁盘读同长，token 一个不少，只省本地 IO）：

  1. 分页死循环（直接死因）：平台 read_file 分页截断（每次 100 行），模型
     携 offset 续读时，缓存键只取 file_path、无视分页参数 → 续读永远命中
     第一页缓存，拿回同一份前 100 行。trace-3d0d75f283524df9a4787ec871ae3512
     显示模型在 specs/SKILL.md（8043 字符）上反复「继续读取剩余部分」打转。
     增量流程必读自己写的长 storyline.md，同构必中。
  2. TTL 内读到旧内容（D2 事故类）：写后失效钩子只覆盖 write_file/edit_file，
     任何失效缺口都会让模型基于改前旧内容做后续 edit → old_string 不匹配
     → 连环失败。

  进化 session 54c11df3179a 拍板（进化点 #3，变体 B·停缓存留哨兵）：
  内容永远直读磁盘，只保留「重复读哨兵」——同参数同内容的重复读取在
  响应文首加一行诚实信号头，保留 #8 治理成果（trace-be5d2ddd：模型重读
  同一文件 8 遍毫无阻力曾是独立事故，哨兵是打转行为的唯一预警）。

哨兵语义：
  - 所有 read_file 永远直读磁盘（handler 透传，offset/limit 原样通过）；
  - 每路径只记 sha256 内容哈希 + 重复计数，不存内容——无 TTL、无淘汰、
    无失效一致性问题；
  - 同参数、同内容的重复读 → 响应文首加信号头「本会话第 N 次读取同一
    文件 · 内容未变」（不承诺「全文」——旧版「全文如下」是虚假承诺，
    缓存里存的可能是截断版）；
  - 内容变化或写后重读 → 计数归零视为新读（写后重读是正当的）。

兼容性（解释器基础链环节，不可物理删除的约束）：
  - 保留模块路径 middleware/read_cache.py；
  - 保留类名 ReadCacheMiddleware 与别名 ReadSentinelMiddleware；
  - 保留顶层 build(abc) 挂载钩子（装配签名不变）。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)


class ReadSentinelMiddleware(AgentMiddleware):
    """读哨兵中间件：read_file 直读磁盘，只跟踪重复读取。

    不缓存内容。per-path 状态仅两样：sha256 内容哈希、重复计数。
    目的：消灭分页死循环（根因 = 内容缓存），保留打转预警（信号头）。
    """

    def __init__(
        self,
        *,
        intervention_callback: Callable[..., None] | None = None,
    ) -> None:
        self.intervention_callback = intervention_callback
        # 文件路径 → 最近一次读取的 sha256 十六进制
        self._content_hash: dict[str, str] = {}
        # 文件路径 → 重复读取次数（首次读为 1；同参数同内容再读才 +1）
        self._repeat_counts: dict[str, int] = {}
        # (文件路径, 参数指纹) 已见集合——同参数重复读才算打转
        # （v38 重写时漏初始化，此处补上；bug ② 修复）
        self._args_seen: set[tuple[str, str]] = set()
        # 上报计数（防干预回调被高频触发刷屏）
        self._signaled: int = 0

    # ------------------------------------------------------------------
    # 工具调用拦截（同步 / 异步）
    # ------------------------------------------------------------------

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        """拦截同步工具调用：read_file 直读磁盘 + 重复读信号；写后计数归零。"""
        tool_kind = self._classify_tool(request)

        # write_file / edit_file：写后重读是正当的（内容已变），计数与哈希清空
        if tool_kind == "write":
            result = handler(request)
            self._reset_for_request(request)
            return result

        # 非 read_file：完全透传
        if tool_kind != "read":
            return handler(request)

        result = handler(request)
        return self._decorate_result(request, result)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        """拦截异步工具调用：read_file 直读磁盘 + 重复读信号；写后计数归零。"""
        tool_kind = self._classify_tool(request)

        if tool_kind == "write":
            result = await handler(request)
            self._reset_for_request(request)
            return result

        if tool_kind != "read":
            return await handler(request)

        result = await handler(request)
        return self._decorate_result(request, result)

    # ------------------------------------------------------------------
    # 重复读检测
    # ------------------------------------------------------------------

    def _decorate_result(self, request: Any, result: Any) -> Any:
        """对 read_file 的磁盘结果做重复读检测：同参数同内容 → 加信号头。"""
        file_path = self._get_file_path(request)
        if file_path is None:
            return result

        content = self._extract_content(result)
        if content is None:
            return result

        key = str(file_path)
        new_hash = self._sha256(content)

        prev_hash = self._content_hash.get(key)
        args_key = self._args_fingerprint(request)
        seen_key = (key, args_key)

        # 同内容 + 同参数（此前至少读过一次该参数）→ 重复读
        # （判定用元组键 seen_key，与存入 _args_seen 的形态一致——v38
        # 重写时误用裸字符串 args_key 对元组集合恒 False，哨兵信号永不
        # 触发；bug ③ 修复）
        if (
            prev_hash == new_hash
            and seen_key in self._args_seen
        ):
            self._repeat_counts[key] = self._repeat_counts.get(key, 1) + 1
            count = self._repeat_counts[key]
            logger.debug(
                "ReadSentinel REPEAT: %s (count=%d, args=%s)",
                file_path, count, args_key,
            )
            self._emit_repeated_read()
            return self._make_signaled_response(request, content, count)

        # 新参数或内容已变化：记录指纹，计数重置为 1（视为新读）
        self._content_hash[key] = new_hash
        self._args_seen.add(seen_key)
        if self._repeat_counts.get(key) != 1:
            self._repeat_counts[key] = 1
        return result

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _classify_tool(self, request: Any) -> str:
        """分类工具调用：'read' / 'write' / 'other'。"""
        tool_call = getattr(request, "tool_call", {})
        tool_name = str(_mapping_value(tool_call, "name") or "")
        if tool_name == "read_file":
            return "read"
        if tool_name in ("write_file", "edit_file"):
            return "write"
        return "other"

    def _reset_for_request(self, request: Any) -> None:
        """写后清空：下一次 read 必然走新读路径（内容已变）。"""
        file_path = self._get_file_path(request)
        if file_path is None:
            return
        key = str(file_path)
        self._content_hash.pop(key, None)
        self._repeat_counts.pop(key, None)
        logger.debug("ReadSentinel RESET on write: %s", file_path)

    def _get_file_path(self, request: Any) -> Path | None:
        """从工具调用中提取文件路径。"""
        tool_call = getattr(request, "tool_call", {})
        args = _mapping_value(tool_call, "args")
        if not isinstance(args, dict):
            return None
        path_str = args.get("file_path") or args.get("path") or ""
        if not isinstance(path_str, str) or not path_str:
            return None
        return Path(path_str)

    def _args_fingerprint(self, request: Any) -> str:
        """read_file 除 file_path 外的参数指纹（offset/limit 等）。

        同参数重复读才算打转；分页交错读（第 1 页 ↔ 第 2 页）各自正常
        计新读，不互相误报。
        """
        tool_call = getattr(request, "tool_call", {})
        args = _mapping_value(tool_call, "args")
        if not isinstance(args, dict):
            return ""
        parts = [
            f"{k}={args[k]!r}"
            for k in sorted(args)
            if k not in ("file_path", "path")
        ]
        return ";".join(parts)

    def _extract_content(self, result: Any) -> str | None:
        """从工具调用结果中提取文本内容。"""
        if isinstance(result, str):
            return result
        if isinstance(result, ToolMessage):
            content = result.content
            # 无 file_path 的调用根本不会走到这里
            if isinstance(content, str):
                return content
        return None

    def _make_signaled_response(self, request: Any, content: str, count: int) -> ToolMessage:
        """构造重复读信号响应：内容照给（来自磁盘），文首加一行元信息。

        诚实语义：只说「内容未变」，不承诺「全文」——读取是分页的，
        返回内容可能只是文件的一页。
        """
        tool_call = getattr(request, "tool_call", {})
        tool_call_id = _mapping_value(tool_call, "id")
        return ToolMessage(
            content=(
                f"[read_sentinel] 本会话第 {count} 次读取同一文件（相同参数）· 内容未变\n\n"
                + content
            ),
            name="read_file",
            tool_call_id=str(tool_call_id or ""),
       )

    def _emit_repeated_read(self) -> None:
        if self.intervention_callback is None:
            return
        self._signaled += 1
        try:
            self.intervention_callback(
                action="repeated_read",
                hook="wrap_tool_call",
                affected_fields=["tool_output"],
                reason="read_sentinel_repeated_read",
            )
        except Exception:
            pass

    @property
    def stats(self) -> dict[str, int]:
        """返回重复读统计（兼容旧 stats 属性访问方）。"""
        return {
            "repeated_reads": sum(c - 1 for c in self._repeat_counts.values() if c > 1),
            "signaled": self._signaled,
        }

    @staticmethod
    def _sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _mapping_value(mapping: object, key: str) -> Any:
    """安全地从字典或对象中取值（与其它中间件一致的取值方式）。

    v38 重写时调用点抄来、定义漏带（4 处调用 0 处定义），每次工具调用
    必崩 NameError（trace-b39c83df66fc4f7e：ls/glob/read_file 全灭）——
    bug ① 修复：按代码库惯例（其余中间件均自带）补回定义。
    """
    if isinstance(mapping, dict):
        return mapping.get(key)
    return getattr(mapping, key, None)


# 兼容别名：解释器若按类名 ReadCacheMiddleware import，行为等价（哨兵版）
ReadCacheMiddleware = ReadSentinelMiddleware

__all__ = ["ReadSentinelMiddleware", "ReadCacheMiddleware"]


def build(abc):
    """架构清单挂载钩子：基础链读哨兵（原 ReadCache 缓存位，直读磁盘 + 重复读信号）。"""
    return ReadSentinelMiddleware(intervention_callback=abc.intervention_callback)
