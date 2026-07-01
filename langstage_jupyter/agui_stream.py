"""Experimental in-process AG-UI streaming path for the Jupyter sidebar.

ADR 0002 (cli-first pattern, now jupyter): drive the agent through the official
``ag-ui-langgraph`` adapter in-process (no web server) and map AG-UI events onto
the same chunk dicts the frontend already consumes from ``stream_graph_updates``
— so the labextension is unchanged. Text, tool calls/results, and interrupts
(display + resume via ``forwarded_props.command.resume``) are all supported.

NOTE: the AG-UI->chunk-dict mapping now lives in the core
(``langgraph_stream_parser.agui.iter_chunk_frames``, 0.6.17) and is shared with
langstage-cli; this module keeps only the thin session/pump wrappers.

Requires the ``agui`` extra::

    pip install "langstage-jupyter[agui]"
"""
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

    The mapping itself lives in the core (``agui.iter_chunk_frames``, 0.6.17) —
    shared with langstage-cli — so a rendering fix lands once.
    """
    from langgraph_stream_parser.agui import iter_chunk_frames

    async for frame in iter_chunk_frames(agent, message, thread_id, resume=resume):
        yield frame


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
