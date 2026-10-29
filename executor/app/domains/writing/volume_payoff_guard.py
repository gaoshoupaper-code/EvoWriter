"""卷纲候选的有限修复与受控提交；检查器和改稿器由运行时注入。"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from uuid import uuid4

from app.domains.writing.retry import WriterRetryError

from contracts.writing_artifacts import (
    KIND_VOLUME,
    VOLUME_PAYOFF_CHECKS_RELPATH,
    artifact_relpath,
    is_final_status,
    validate_volume_final,
    volume_draft_relpath,
)

_LOCKS: dict[str, threading.RLock] = {}
_LOCK_REGISTRY = threading.Lock()
_ACTIVE_OPERATIONS: set[tuple[str, int, str]] = set()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(text.encode("utf-8"))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _workspace_lock(workspace: Path):
    with _LOCK_REGISTRY:
        return _LOCKS.setdefault(str(workspace.resolve()), threading.RLock())


def _load_journal(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 1, "volumes": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("schema_version") != 1
            or not isinstance(data.get("volumes"), dict)):
        raise ValueError("卷纲检查记录损坏")
    return data


def bind_volume_operation(
    workspace: Path, trace_id: str, message_id: str | None = None, *, new_revision: bool = False,
) -> str:
    """只由服务端已接纳的用户消息创建修订编号；普通恢复沿用当前编号。"""
    if not isinstance(trace_id, str) or not trace_id:
        raise ValueError("修复任务缺少服务端trace编号")
    workspace = Path(workspace)
    path = workspace / VOLUME_PAYOFF_CHECKS_RELPATH
    with _workspace_lock(workspace):
        journal = _load_journal(path)
        bindings = journal.setdefault("trace_operations", {})
        if not isinstance(bindings, dict):
            raise ValueError("修订任务绑定记录损坏")
        existed = trace_id in bindings
        binding = bindings.setdefault(trace_id, {"current": trace_id, "revisions": {}})
        if (not isinstance(binding, dict) or not isinstance(binding.get("current"), str)
                or not isinstance(binding.get("revisions"), dict)):
            raise ValueError("修订任务绑定记录损坏")
        if new_revision and message_id:
            previous = binding["revisions"].get(message_id)
            if previous is not None:
                return previous
            operation_id = f"{trace_id}:revision:{_hash(message_id)[:16]}"
            binding["revisions"][message_id] = operation_id
            binding["current"] = operation_id
        else:
            operation_id = binding["current"]
            if existed:
                return operation_id
        _atomic_write(path, json.dumps(journal, ensure_ascii=False, indent=2))
        return operation_id


def _binding_is_current(journal: dict, operation_id: str) -> bool:
    bindings = journal.get("trace_operations", {})
    if not isinstance(bindings, dict):
        raise ValueError("修订任务绑定记录损坏")
    for trace_id, binding in bindings.items():
        if not isinstance(binding, dict) or not isinstance(binding.get("revisions"), dict):
            raise ValueError("修订任务绑定记录损坏")
        if operation_id == trace_id or operation_id in binding["revisions"].values():
            return operation_id == binding.get("current")
    return True


def _within_scope(text: str, scope: dict | None) -> bool:
    if scope is None:
        return True
    prefix, suffix = scope.get("prefix"), scope.get("suffix")
    return (
        isinstance(prefix, str) and isinstance(suffix, str)
        and len(text) >= len(prefix) + len(suffix)
        and text.startswith(prefix) and text.endswith(suffix)
    )


def _draft_text(text: str, reason: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines[:8]):
        if line.strip().startswith(">") and "状态" in line:
            date = line.partition("｜")[2]
            lines[index] = "> 状态：起草中" + ("｜" + date if date else "")
            break
    else:
        lines.insert(1 if lines else 0, "> 状态：起草中")
    lines = [line for line in lines if not line.startswith("> 爽点检查未通过：")]
    lines.insert(2 if len(lines) >= 2 else len(lines), "> 爽点检查未通过：" + reason.replace("\n", " "))
    return "\n".join(lines) + "\n"


class VolumePayoffGuard:
    """同一服务端任务与卷号共用持久预算，工具不能另起修复过程。"""

    def __init__(
        self, workspace_path: Path, *, operation_id: str, policy_hash: str,
        check, repair, policy_status: str = "ready", max_repairs: int = 2,
        is_active=None,
    ) -> None:
        self.workspace = Path(workspace_path)
        self.operation_id = operation_id
        self.policy_hash = policy_hash
        self.check = check
        self.repair = repair
        self.policy_status = policy_status
        self.max_repairs = max_repairs
        self.is_active = is_active
        self.journal_path = self.workspace / VOLUME_PAYOFF_CHECKS_RELPATH
        self._lock = _workspace_lock(self.workspace)

    def _journal(self) -> dict:
        return _load_journal(self.journal_path)

    def _write_journal(self, journal: dict) -> None:
        _atomic_write(self.journal_path, json.dumps(journal, ensure_ascii=False, indent=2))

    def _assert_active(self) -> None:
        if self.is_active is None:
            return
        try:
            active = self.is_active()
        except Exception:
            active = False
        if not active:
            raise RuntimeError("本次写作已停止，候选仅保留为草稿。")

    def _result(self, status, path, content, reason, operation, violations=None) -> dict:
        return {
            "status": status, "path": path, "content": content, "reason": reason,
            "violations": violations or [],
            "repair_attempts": operation.get("repair_attempts", 0),
            "candidate_hash": operation.get("candidate_hash", _hash(content)),
            "policy_hash": self.policy_hash,
        }

    def _persist(self, volume: int, operation: dict) -> None:
        with self._lock:
            journal = self._journal()
            record = journal["volumes"][str(volume)]
            if (record["active_operation"]["operation_id"] != self.operation_id
                    or not _binding_is_current(journal, self.operation_id)):
                raise ValueError("当前卷已被新的修改任务接管")
            record["active_operation"] = operation
            self._write_journal(journal)

    def _retain_draft(
        self, volume: int, candidate: str, operation: dict, reason: str,
        *, status: str = "blocked", violations: list | None = None,
    ) -> dict:
        rel = artifact_relpath(KIND_VOLUME, volume=volume)
        with self._lock:
            journal = self._journal()
            record = journal["volumes"][str(volume)]
            if (record["active_operation"]["operation_id"] != self.operation_id
                    or not _binding_is_current(journal, self.operation_id)):
                return self._result("blocked", "", "", "当前卷已有新修改，候选未覆盖。", operation)
            current = _read(self.workspace / rel)
            # 正式稿、并发新内容都不可被失败候选替换。
            if (is_final_status(current) or _hash(current) != operation["base_hash"]
                    or operation.get("from_draft")):
                rel = volume_draft_relpath(volume)
            content = _draft_text(candidate, reason)
            operation.update(status=status, reason=reason, candidate_hash=_hash(candidate))
            if status == "retryable":
                operation["candidate_text"] = candidate
            else:
                operation.pop("candidate_text", None)
            result = self._result(status, rel, content, reason, operation, violations)
            operation["result"] = result
            record["active_operation"] = operation
            record["draft_base_hash"] = operation["base_hash"]
            self._write_journal(journal)
            _atomic_write(self.workspace / rel, content)
            if rel == artifact_relpath(KIND_VOLUME, volume=volume):
                operation["base_hash"] = _hash(content)
            record["draft_path"] = rel
            record["draft_hash"] = _hash(content)
            self._write_journal(journal)
            return result

    def _submit(self, volume: int, candidate: str, operation: dict, *, constrained: bool) -> dict:
        self._assert_active()
        if not is_final_status(candidate):
            return self._retain_draft(
                volume, candidate, operation, "卷纲仍处起草中，尚未申请定稿。", status="draft",
            )
        rel = artifact_relpath(KIND_VOLUME, volume=volume)
        with self._lock:
            journal = self._journal()
            record = journal["volumes"][str(volume)]
            if (record["active_operation"]["operation_id"] != self.operation_id
                    or not _binding_is_current(journal, self.operation_id)):
                return self._result("blocked", "", "", "当前卷已有新修改，候选未覆盖。", operation)
            if _hash(_read(self.workspace / rel)) != operation["base_hash"]:
                return self._retain_draft(volume, candidate, operation, "检查期间原稿已变化，请基于新稿合并。")
            operation.update(status="repairing", candidate_hash=_hash(candidate), reason="待提交")
            record["active_operation"] = operation
            self._write_journal(journal)
            path = self.workspace / rel
            original_bytes = path.read_bytes() if path.exists() else None
            original_hash = operation["base_hash"]
            try:
                self._assert_active()
                _atomic_write(path, candidate)
                if constrained:
                    record["accepted_receipt"] = {
                        "content_hash": _hash(candidate), "policy_hash": self.policy_hash,
                        "operation_id": self.operation_id,
                    }
                    record.pop("unconstrained_content_hash", None)
                else:
                    record["unconstrained_content_hash"] = _hash(candidate)
                record["legacy_content_hash"] = None
                operation.update(status="passed", reason="", base_hash=_hash(candidate))
                operation.pop("candidate_text", None)
                result = self._result("committed", rel, candidate, "", operation)
                operation["result"] = result
                self._write_journal(journal)
                return result
            except Exception:
                # 只撤回本次已写内容；不得覆盖提交后出现的新字节。
                if _hash(_read(path)) == _hash(candidate):
                    if original_bytes is None:
                        path.unlink(missing_ok=True)
                    else:
                        _atomic_write(path, original_bytes.decode("utf-8"))
                operation["base_hash"] = original_hash
                raise

    def commit(
        self, volume: int, base_text: str, candidate_text: str, source: str,
        operation_id: str | None = None, *, draft: bool = False,
        edit_scope: dict | None = None,
    ) -> dict:
        key = (str(self.workspace.resolve()), volume, self.operation_id)
        with _LOCK_REGISTRY:
            if key in _ACTIVE_OPERATIONS:
                return self._result("blocked", "", "", "本卷同一任务的检查正在进行，请等待当前结果。", {})
            _ACTIVE_OPERATIONS.add(key)
        try:
            return self._commit(
                volume, base_text, candidate_text, source, operation_id,
                draft=draft, edit_scope=edit_scope,
            )
        finally:
            with _LOCK_REGISTRY:
                _ACTIVE_OPERATIONS.discard(key)

    def _commit(
        self, volume: int, base_text: str, candidate_text: str, source: str,
        operation_id: str | None, *, draft: bool,
        edit_scope: dict | None,
    ) -> dict:
        operation: dict = {"repair_attempts": 0}
        if operation_id is not None and operation_id != self.operation_id:
            return self._result("blocked", "", "", "工具不能更换服务端修复任务编号。", operation)
        if source not in ("full", "edit", "rewrite") or not candidate_text.strip():
            return self._result("blocked", "", "", "卷纲提交来源或内容无效。", operation)
        try:
            rel = artifact_relpath(KIND_VOLUME, volume=volume)
            with self._lock:
                journal = self._journal()
                current = _read(self.workspace / rel)
                if not _binding_is_current(journal, self.operation_id):
                    return self._result("blocked", "", "", "该修订已被新的用户任务替代，不能重新提交。", operation)
                record = journal["volumes"].setdefault(str(volume), {
                    "accepted_receipt": None,
                    "legacy_content_hash": _hash(current) if is_final_status(current) else None,
                })
                prior = record.get("active_operation") or {}
                same_operation = prior.get("operation_id") == self.operation_id
                if same_operation:
                    operation = prior
                    self._assert_active()
                    if operation.get("policy_hash") != self.policy_hash:
                        return self._result("blocked", "", "", "恢复任务的规则已变化，请发起新修改。", operation)
                    if operation.get("status") == "blocked":
                        return operation["result"]
                    cached = operation.get("result")
                    if (operation.get("status") != "retryable"
                            and operation.get("input_hash") == _hash(candidate_text) and cached):
                        if _hash(_read(self.workspace / cached["path"])) == _hash(cached["content"]):
                            return cached
                elif self.operation_id in record.get("previous_operations", {}):
                    previous = record["previous_operations"][self.operation_id]
                    return self._result("blocked", "", "", "该修复任务已被新任务替代。", previous)

                resuming = same_operation and operation.get("status") in ("repairing", "retryable")
                if not resuming:
                    base_path = self.workspace / (volume_draft_relpath(volume) if draft else rel)
                    if _read(base_path) != base_text:
                        return self._result("blocked", "", "", "原稿或草稿已变化，请重新读取。", operation)
                    if draft and record.get("draft_base_hash") != _hash(current):
                        return self._result("blocked", "", "", "修订草稿对应的正式稿已变化，请重新合并。", operation)
                    if not same_operation:
                        if prior:
                            record.setdefault("previous_operations", {})[prior["operation_id"]] = {
                                "repair_attempts": prior.get("repair_attempts", 0),
                            }
                        operation = {"operation_id": self.operation_id, "repair_attempts": 0}
                    operation.update(
                        policy_hash=self.policy_hash, base_hash=_hash(current),
                        input_hash=_hash(candidate_text), candidate_hash=_hash(candidate_text),
                        candidate_text=candidate_text, from_draft=draft,
                        status="repairing", reason="检查中",
                    )
                    record["active_operation"] = operation
                    self._write_journal(journal)
                else:
                    candidate_text = operation["candidate_text"]
                    if _hash(current) != operation["base_hash"]:
                        return self._retain_draft(volume, candidate_text, operation, "恢复时原稿已变化，请重新合并。")

            self._assert_active()
            if not _within_scope(candidate_text, edit_scope):
                return self._retain_draft(volume, candidate_text, operation, "候选超出获准的局部修改范围。")
            if self.policy_status == "not_applicable":
                return self._submit(volume, candidate_text, operation, constrained=False)
            if self.policy_status != "ready" or not self.policy_hash:
                return self._retain_draft(volume, candidate_text, operation, "爽点定义不可用或存在歧义，不能无约束定稿。")
            if not is_final_status(candidate_text) and validate_volume_final(candidate_text):
                return self._retain_draft(
                    volume, candidate_text, operation, "卷纲尚未完整，继续起草后再检查。", status="draft",
                )

            while True:
                fingerprint = _hash(candidate_text)
                outcome = operation.get("last_verdict")
                if not outcome or outcome.get("candidate_hash") != fingerprint:
                    self._assert_active()
                    outcome = self.check(candidate_text)
                    self._assert_active()
                    if (not isinstance(outcome, dict) or type(outcome.get("passed")) is not bool
                            or outcome.get("candidate_hash") != fingerprint
                            or outcome.get("policy_hash") != self.policy_hash
                            or not isinstance(outcome.get("violations"), list)):
                        raise ValueError("检查结果缺失或内容、规则指纹不匹配")
                    operation["last_verdict"] = outcome
                    self._persist(volume, operation)
                errors = list(outcome["violations"])
                errors.extend(validate_volume_final(candidate_text))
                if outcome["passed"] and not errors:
                    return self._submit(volume, candidate_text, operation, constrained=True)
                if not errors:
                    errors = ["内容复核未通过"]
                if operation["repair_attempts"] >= self.max_repairs:
                    reasons = [str(error.get("message", error)) if isinstance(error, dict) else str(error) for error in errors]
                    return self._retain_draft(
                        volume, candidate_text, operation,
                        "自动修正已达上限：" + "；".join(reasons), violations=errors,
                    )
                # 先记账再调用模型，中断和重新装配都不能补回额度。
                self._assert_active()
                operation["repair_attempts"] += 1
                self._persist(volume, operation)
                self._assert_active()
                repaired = self.repair(candidate_text, errors)
                self._assert_active()
                if not isinstance(repaired, str) or not repaired.strip():
                    raise ValueError("自动修正没有返回完整卷纲")
                if not _within_scope(repaired, edit_scope):
                    return self._retain_draft(volume, candidate_text, operation, "合规修复需要超出获准的局部范围，已保留草稿。")
                candidate_text = repaired
                operation.update(candidate_text=repaired, candidate_hash=_hash(repaired))
                self._persist(volume, operation)
        except Exception as exc:
            reason = f"卷纲检查或提交失败（{type(exc).__name__}）：{exc}"
            if "base_hash" in operation:
                try:
                    retryable = (
                        isinstance(exc, WriterRetryError) and exc.is_retryable
                        and not exc.had_partial_response
                    )
                    return self._retain_draft(
                        volume, candidate_text, operation, reason,
                        status="retryable" if retryable else "blocked",
                    )
                except Exception:
                    pass
            return self._result("blocked", "", "", reason, operation)


__all__ = ["VolumePayoffGuard", "bind_volume_operation"]
