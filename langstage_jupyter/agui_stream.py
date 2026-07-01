"""Experimental in-process AG-UI streaming path for the Jupyter sidebar.

ADR 0002 (cli-first pattern, now jupyter): drive the agent through the official
``ag-ui-langgraph`` adapter in-process (no web server) and map AG-UI events onto
the same chunk dicts the frontend already consumes from ``stream_graph_updates``
— so the labextension is unchanged. Text, tool calls/results, and interrupts
(display + resume via ``forwarded_props.command.resume``) are all supported.

NOTE: this mapping is intentionally identical to ``langstage_cli.agui_stream``.
When a third surface (vscode/web) adopts it, hoist the shared mapping into the
core's ``langgraph_stream_parser.agui`` module and drop the copies.

Requires the ``agui`` extra::

    pip install "langstage-jupyter[agui]"
"""
import json
import uuid
from typing import Any, AsyncIterator, Dict

_IMPORT_HINT = 'the AG-UI path needs the agui extra: pip install "langstage-jupyter[agui]"'


def ensure_agui_available() -> None:
    """Raise a clean, actionable error if the AG-UI adapter isn't installed."""
    try:
        import ag_ui_langgraph  # noqa: F401
        from langgraph_stream_parser.agui import build_agent  # noqa: F401
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(_IMPORT_HINT) from e


def build_session_agent(graph: Any, *, name: str = "langstage-jupyter") -> Any:
    """Wrap a compiled graph once (checkpointer attached by the core bridge) so
    multi-turn memory persists; thread_id is passed per run for per-chat state."""
    ensure_agui_available()
    from langgraph_stream_parser.agui import build_agent

    return build_agent(graph, name=name)


async def agui_stream_updates(
    agent: Any, message: str, thread_id: str, resume: Any = None
) -> AsyncIterator[Dict[str, Any]]:
    """Drive ``agent.run()`` in-process and yield ``stream_graph_updates``-shaped chunks.

    Maps: TextMessageContent -> text; ToolCall{Start,Args,End} -> tool_calls;
    ToolCallResult -> tool_result; CustomEvent(on_interrupt) -> interrupt;
    RunError -> error; one-shot MessagesSnapshot -> text. Ends with ``complete``.

    ``resume`` (a decision answering an interrupt) is delivered as
    ``forwarded_props.command.resume`` -> LangGraph ``Command(resume=...)``.
    """
    from ag_ui.core.types import RunAgentInput, UserMessage

    forwarded_props: Dict[str, Any] = {}
    if resume is not None:
        forwarded_props = {"command": {"resume": resume}}

    run_input = RunAgentInput(
        thread_id=thread_id,
        run_id=str(uuid.uuid4()),
        state={},
        messages=[UserMessage(id=str(uuid.uuid4()), role="user", content=message)],
        tools=[],
        context=[],
        forwarded_props=forwarded_props,
    )

    streamed_text = False
    tool_buf: Dict[str, Dict[str, str]] = {}

    async for ev in agent.run(run_input):
        t = type(ev).__name__
        if t == "TextMessageContentEvent":
            streamed_text = True
            yield {"status": "streaming", "chunk": ev.delta, "node": "agent"}
        elif t == "ToolCallStartEvent":
            tool_buf[ev.tool_call_id] = {"name": ev.tool_call_name, "args": ""}
        elif t == "ToolCallArgsEvent":
            tool_buf.setdefault(ev.tool_call_id, {"name": "tool", "args": ""})["args"] += ev.delta
        elif t == "ToolCallEndEvent":
            tc = tool_buf.pop(ev.tool_call_id, {"name": "tool", "args": ""})
            try:
                args = json.loads(tc["args"]) if tc["args"] else {}
            except json.JSONDecodeError:
                args = {"_raw": tc["args"]}
            yield {"status": "streaming", "tool_calls": [{"name": tc["name"], "args": args}]}
        elif t == "ToolCallResultEvent":
            yield {"status": "streaming", "tool_result": getattr(ev, "content", "")}
        elif t == "CustomEvent" and getattr(ev, "name", None) == "on_interrupt":
            payload = getattr(ev, "value", None)
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {"action_requests": []}
            yield {"status": "interrupt", "interrupt": payload or {"action_requests": []}}
        elif t == "MessagesSnapshotEvent" and not streamed_text:
            for m in ev.messages:
                if getattr(m, "role", None) == "assistant" and getattr(m, "content", None):
                    yield {"status": "streaming", "chunk": m.content, "node": "agent"}
        elif t == "RunErrorEvent":
            yield {"status": "error", "error": getattr(ev, "message", "unknown error")}

    yield {"status": "complete"}


def stream_updates_sync(agent: Any, message: str, thread_id: str, resume: Any = None):
    """Sync bridge: pump the async AG-UI generator one chunk at a time.

    ``AgentWrapper.execute`` is a sync generator that the server handler runs in a
    worker thread (no running event loop), so a fresh loop here is safe and keeps
    streaming lazy (yield per chunk, not collect-then-yield).
    """
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        agen = agui_stream_updates(agent, message, thread_id, resume=resume)
        while True:
            try:
                yield loop.run_until_complete(agen.__anext__())
            except StopAsyncIteration:
                break
    finally:
        loop.close()
