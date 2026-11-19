from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from app.domains.writing.creative_scope import CALL_ID


class InstructionScopeMiddleware(AgentMiddleware):
    """Bind tool side effects to the instruction and the checkpointed call ID."""
    def __init__(self, task, recorder, trace_id):
        self.task = task
        self.recorder = recorder
        self.trace_id = trace_id

    def _check(self, request):
        call = request.tool_call
        reason = self.task.require_write(call["name"], call.get("args") or {})
        if reason:
            if self.trace_id:
                self.recorder.record_intervention(
                    self.trace_id, self.task.agent, action="deny_artifact_tool", hook="wrap_tool_call",
                    middleware_name=type(self).__name__, affected_fields=["tool_call"], reason=reason)
            return ToolMessage(content=reason, status="error", tool_call_id=call["id"])
        return None

    def _prior_output(self, call):
        if call["name"] == "save_artifact" and hasattr(self.task, "publish_tool_output"):
            return self.task.capture_tool_output(call["args"])
        return None

    def _publish(self, call, prior, result):
        if (call["name"] == "save_artifact" and hasattr(self.task, "publish_tool_output")
                and isinstance(result, ToolMessage) and result.status != "error"):
            receipt = self.task.publish_tool_output(call["args"], prior)
            if isinstance(receipt, str) and receipt.startswith("[拒绝]"):
                return ToolMessage(content=receipt, status="error", tool_call_id=call["id"])
        return result

    def wrap_tool_call(self, request, handler):
        if denied := self._check(request):
            return denied
        call = request.tool_call
        token = CALL_ID.set(call["id"])
        try:
            prior = self._prior_output(call)
            return self._publish(call, prior, handler(request))
        finally:
            CALL_ID.reset(token)

    async def awrap_tool_call(self, request, handler):
        if denied := self._check(request):
            return denied
        call = request.tool_call
        token = CALL_ID.set(call["id"])
        try:
            prior = self._prior_output(call)
            return self._publish(call, prior, await handler(request))
        finally:
            CALL_ID.reset(token)
