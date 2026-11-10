"""In-process AG-UI streaming path for the Jupyter sidebar — the wrapper's only
streaming path since core 1.0 (ADR 0003).

Drives the agent through the official ``ag-ui-langgraph`` adapter in-process (no
web server) and maps AG-UI events onto the ``chunk``-dict shape the React frontend
consumes — so the labextension is unchanged. Text, tool calls/results, and
interrupts (display + resume via ``forwarded_props.command.resume``) are all
supported.

NOTE: the AG-UI->chunk-dict mapping lives in the core
(``langstage_core.agui.iter_chunk_frames``) and is shared with langstage-cli;
this module keeps only the thin session/pump wrappers. The AG-UI runtime is a
base dependency (core's ``[agui]`` extra), so ``ensure_agui_available`` is a
belt-and-suspenders guard rather than an opt-in gate.
"""
from typing import Any, AsyncIterator, Callable, Dict, Optional

_IMPORT_HINT = 'the AG-UI path needs the agui extra: pip install "langstage-jupyter[agui]"'


def ensure_agui_available() -> None:
    """Raise a clean, actionable error if the AG-UI adapter isn't installed."""
    try:
        import ag_ui_langgraph  # noqa: F401
        from langstage_core.agui import build_agent  # noqa: F401
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(_IMPORT_HINT) from e


def build_session_agent(graph: Any, *, name: str = "langstage-jupyter") -> Any:
    """Wrap a compiled graph once (checkpointer attached by the core bridge) so
    multi-turn memory persists; thread_id is passed per run for per-chat state."""
    ensure_agui_available()
    from langstage_core.agui import build_agent

    return build_agent(graph, name=name)


async def agui_stream_updates(
    agent: Any, message: str, thread_id: str, resume: Any = None
) -> AsyncIterator[Dict[str, Any]]:
    """Drive ``agent.run()`` in-process and yield ``chunk``-dict updates.

    Maps: TextMessageContent -> text; ToolCall{Start,Args,End} -> tool_calls;
    ToolCallResult -> tool_result; CustomEvent(on_interrupt) -> interrupt;
    RunError -> error; one-shot MessagesSnapshot -> text. Ends with ``complete``.

    ``resume`` (a decision answering an interrupt) is delivered as
    ``forwarded_props.command.resume`` -> LangGraph ``Command(resume=...)``.

    The mapping itself lives in the core (``agui.iter_chunk_frames``, 0.6.17) —
    shared with langstage-cli — so a rendering fix lands once.
    """
    from langstage_core.agui import iter_chunk_frames

    async for frame in iter_chunk_frames(agent, message, thread_id, resume=resume):
        yield frame


# The frame a cancelled turn ends with: what the sidebar has always shown on Stop.
CANCELLED_FRAME: Dict[str, Any] = {"status": "cancelled", "message": "Execution cancelled by user"}

# How often a turn that is waiting on its next frame checks whether Stop was pressed.
_CANCEL_POLL_INTERVAL = 0.05


async def _anext_or_cancel(
    agen: AsyncIterator[Dict[str, Any]],
    should_cancel: Callable[[], bool],
    poll_interval: float,
) -> Any:
    """Await the next frame, but abort the turn if ``should_cancel`` fires meanwhile (gh #200).

    The next frame is awaited as its own task so the loop can wake on a timer and
    check ``should_cancel`` instead of blocking inside one ``__anext__`` until the
    in-flight node finishes. On cancel the task is cancelled, which throws
    ``CancelledError`` into the ag-ui run at its current await point: the graph run
    is torn down there, so no later graph step starts. Returns ``None`` when the turn
    was cancelled; otherwise the frame (or ``StopAsyncIteration`` at the natural end).
    Same approach as langstage-vscode's sidecar (vscode #93).
    """
    import asyncio

    task = asyncio.ensure_future(agen.__anext__())
    while True:
        done, _ = await asyncio.wait({task}, timeout=poll_interval)
        if task in done:
            return task.result()
        if should_cancel():
            task.cancel()
            try:
                await task
            except BaseException:  # noqa: BLE001 - CancelledError / the agent's error, discarded on cancel
                pass
            return None


def stream_updates_sync(
    agent: Any,
    message: str,
    thread_id: str,
    resume: Any = None,
    *,
    should_cancel: Optional[Callable[[], bool]] = None,
    poll_interval: float = _CANCEL_POLL_INTERVAL,
):
    """Sync bridge: pump the async AG-UI generator one chunk at a time.

    ``AgentWrapper.execute`` is a sync generator that the server handler runs in a
    worker thread (no running event loop), so a fresh loop here is safe and keeps
    streaming lazy (yield per chunk, not collect-then-yield).

    ``should_cancel`` (the sidebar's Stop, gh #200) is checked WHILE each frame is
    awaited. When it fires, the in-flight run is cancelled at once, ``CANCELLED_FRAME``
    is yielded and the stream ends: no later graph step starts after the user was
    told the turn was cancelled. A sync node or tool that is already running in a
    worker thread can't be interrupted from Python; it finishes in the background,
    but its result is discarded and the graph does not go on to the next step.
    """
    import asyncio

    loop = asyncio.new_event_loop()
    agen = agui_stream_updates(agent, message, thread_id, resume=resume)
    try:
        while True:
            try:
                if should_cancel is None:
                    yield loop.run_until_complete(agen.__anext__())
                    continue
                if should_cancel():
                    yield dict(CANCELLED_FRAME)
                    break
                frame = loop.run_until_complete(_anext_or_cancel(agen, should_cancel, poll_interval))
            except StopAsyncIteration:
                break
            if frame is None:
                yield dict(CANCELLED_FRAME)
                break
            yield frame
    finally:
        _close_quietly(loop, agen)


def _close_quietly(loop: Any, agen: Any) -> None:
    """Tear the turn down without "Task was destroyed but it is pending!" noise (gh #200).

    Cancel every still-pending task (the ag-ui run driving the graph) and let it
    unwind FIRST. Only then ``aclose()`` the generator: closing it while the run task
    is still driving it raises "aclose(): asynchronous generator is already running".
    Then shut down async generators and close the loop.
    """
    import asyncio

    try:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    except Exception:  # noqa: BLE001 - best-effort teardown
        pass
    for step in (agen.aclose, loop.shutdown_asyncgens):
        try:
            loop.run_until_complete(step())
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass
    loop.close()
