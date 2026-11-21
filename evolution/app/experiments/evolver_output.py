"""Forward the automated evolver's visible model deltas to the live view."""

from langchain_core.callbacks import BaseCallbackHandler

from app.experiments import live_output


def _summary_parts(block):
    if not isinstance(block, dict) or not isinstance(block.get("summary"), list):
        return
    for part in block["summary"]:
        if isinstance(part, dict) and part.get("type") == "summary_text":
            text = part.get("text")
            if isinstance(text, str) and text:
                yield text


class EvolverOutputCallback(BaseCallbackHandler):
    run_inline = True

    def __init__(self, trace_id):
        self.trace_id = trace_id

    def on_llm_new_token(self, token, *, run_id, chunk=None, **kwargs):
        message = getattr(chunk, "message", None)
        additional = getattr(message, "additional_kwargs", {})
        content = getattr(message, "content", None)
        blocks = content if isinstance(content, list) else [content]
        call_id = str(run_id)

        # LangChain's Responses v1 exposes summaries as typed content. Its v0
        # output adapter puts the same public summary in additional_kwargs.
        summaries = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "reasoning":
                summaries.extend(_summary_parts(block))
        if not summaries:
            summaries.extend(_summary_parts(additional.get("reasoning")))
        for summary in summaries:
            live_output.publish_delta(self.trace_id, call_id, "reasoning", summary)
        if not summaries and isinstance(reasoning := additional.get("reasoning_content"), str) and reasoning:
            live_output.publish_delta(self.trace_id, call_id, "reasoning", reasoning)

        for block in blocks:
            text = block if isinstance(block, str) else None
            if isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
                text = block.get("text")
            if isinstance(text, str) and text:
                live_output.publish_delta(self.trace_id, call_id, "text", text)

    def on_llm_end(self, response, *, run_id, **kwargs):
        live_output.finish_call(self.trace_id, str(run_id))

    def on_llm_error(self, error, *, run_id, **kwargs):
        live_output.finish_call(self.trace_id, str(run_id))
