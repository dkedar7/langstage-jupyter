"""Stop aborts the turn at once (gh #200).

Before: the cancel flag was only checked when the NEXT chunk arrived, and the
generator chain was then abandoned with ``loop.close()``. So "cancelled" showed up
only after the in-flight node finished, the graph's next step could still start and
run its side effects AFTER the user was told the turn was cancelled, that step was
missing from the checkpoint, and every Stop logged "Task was destroyed but it is
pending!" / "aclose(): asynchronous generator is already running".
"""
import asyncio
import gc
import logging
import threading
import time
from unittest.mock import MagicMock

import pytest

pytest.importorskip("ag_ui_langgraph")

from langchain_core.messages import AIMessage  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.graph import START, MessagesState, StateGraph  # noqa: E402

from langstage_jupyter import handlers  # noqa: E402
from langstage_jupyter.agent_wrapper import AgentWrapper  # noqa: E402
from langstage_jupyter.agui_stream import (  # noqa: E402
    CANCELLED_FRAME,
    build_session_agent,
    stream_updates_sync,
)

SLOW = 1.2  # how long step "a" takes; the cancel fires well before it ends


def _two_step_graph(marks: list, *, async_a: bool):
    """a (slow) -> b (side effect). The issue's repro, scaled down."""
    if async_a:
        async def a(state):
            await asyncio.sleep(SLOW)
            marks.append("a done")
            return {"messages": [AIMessage(content="A done")]}
    else:
        def a(state):
            time.sleep(SLOW)
            marks.append("a done")
            return {"messages": [AIMessage(content="A done")]}

    def b(state):
        marks.append("b ran")
        return {"messages": [AIMessage(content="B done")]}

    g = StateGraph(MessagesState)
    g.add_node("a", a)
    g.add_node("b", b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    return g.compile(checkpointer=InMemorySaver())


def _cancel_after(delay: float) -> threading.Event:
    ev = threading.Event()
    threading.Timer(delay, ev.set).start()
    return ev


def _messages(agent, thread_id):
    state = asyncio.run(agent.graph.aget_state({"configurable": {"thread_id": thread_id}}))
    return [(m.type, m.content) for m in state.values.get("messages", [])]


@pytest.mark.parametrize("async_a", [False, True], ids=["sync-node", "async-node"])
def test_stop_ends_the_turn_promptly_and_the_next_step_never_starts(async_a, caplog):
    marks: list = []
    agent = build_session_agent(_two_step_graph(marks, async_a=async_a))
    cancel = _cancel_after(0.3)

    t0 = time.monotonic()
    with caplog.at_level(logging.ERROR, logger="asyncio"):
        frames = list(stream_updates_sync(agent, "go", "t200", should_cancel=cancel.is_set))
        elapsed = time.monotonic() - t0
        # Give a sync node still running in its worker thread time to finish, then
        # make sure the graph did NOT go on to step b.
        time.sleep(SLOW + 0.5)
        gc.collect()

    assert frames[-1] == CANCELLED_FRAME
    assert elapsed < SLOW, f"cancel took {elapsed:.2f}s: it waited for the in-flight node"
    assert "b ran" not in marks, "the graph's next step started after the turn was cancelled"
    if async_a:
        assert "a done" not in marks, "an async node is cancelled at its await point"
    # The checkpoint holds only what the graph completed: the human turn.
    assert _messages(agent, "t200") == [("human", "go")]
    noise = [r.getMessage() for r in caplog.records]
    assert not any("destroyed but it is pending" in m or "already running" in m for m in noise), noise


def test_thread_continues_cleanly_after_a_cancelled_turn():
    marks: list = []
    agent = build_session_agent(_two_step_graph(marks, async_a=True))
    cancel = _cancel_after(0.2)
    list(stream_updates_sync(agent, "first", "t200b", should_cancel=cancel.is_set))

    frames = list(stream_updates_sync(agent, "second", "t200b", should_cancel=lambda: False))
    assert frames[-1]["status"] == "complete"
    assert marks == ["a done", "b ran"]  # only the second, uncancelled turn ran a and b
    assert _messages(agent, "t200b") == [
        ("human", "first"), ("human", "second"), ("ai", "A done"), ("ai", "B done"),
    ]


def test_uncancelled_turn_is_unchanged():
    marks: list = []
    agent = build_session_agent(_two_step_graph(marks, async_a=True))
    frames = list(stream_updates_sync(agent, "go", "t200c", should_cancel=lambda: False))
    assert frames[-1]["status"] == "complete"
    assert CANCELLED_FRAME not in frames
    assert marks == ["a done", "b ran"]


def test_cancel_set_before_the_turn_starts_runs_nothing():
    marks: list = []
    agent = build_session_agent(_two_step_graph(marks, async_a=True))
    frames = list(stream_updates_sync(agent, "go", "t200d", should_cancel=lambda: True))
    assert frames == [CANCELLED_FRAME]
    assert marks == []


def test_wrapper_passes_should_cancel_through_to_the_stream():
    marks: list = []
    w = AgentWrapper.__new__(AgentWrapper)
    w.agent = _two_step_graph(marks, async_a=True)
    w.load_error = None
    w._agui_agent = None
    cancel = _cancel_after(0.2)
    chunks = list(w.stream("go", thread_id="t200e", should_cancel=cancel.is_set))
    assert chunks[-1] == CANCELLED_FRAME
    assert marks == []


class _IgnoresCancel:
    """An agent wrapper that ignores should_cancel and keeps streaming."""

    def __init__(self):
        self.calls = []

    def set_root_dir(self, root_dir):
        pass

    def stream(self, message, thread_id=None, context=None, should_cancel=None):
        self.calls.append(should_cancel)
        for i in range(50):
            time.sleep(0.02)
            yield {"chunk": f"t{i}", "status": "streaming"}
        yield {"status": "complete"}


def test_chat_handler_forwards_nothing_after_stop(monkeypatch):
    from tornado.web import Application

    fake = _IgnoresCancel()
    monkeypatch.setattr(handlers, "get_agent", lambda: fake, raising=False)
    h = handlers.ChatHandler(Application(), MagicMock())
    h._jupyter_current_user = h._current_user = "test"
    h.get_json_body = lambda: {"message": "go", "thread_id": "t200f"}
    written = []
    h.write = lambda data: written.append(data)
    async def flush():
        return None

    h.flush = flush
    h.finish = lambda *a, **k: None

    def stop_soon():
        for _ in range(100):  # wait for the handler to register the turn
            with handlers._execution_lock:
                ev = handlers._active_executions.get("t200f")
            if ev:
                break
            time.sleep(0.01)
        time.sleep(0.15)
        ev.set()

    threading.Thread(target=stop_soon).start()
    asyncio.run(h.post())

    assert callable(fake.calls[0]), "the handler hands the cancel flag to the stream"
    assert '"cancelled"' in written[-1]
    assert not any('"complete"' in w for w in written)
    assert len(written) < 20, "chunks kept flowing after Stop"
