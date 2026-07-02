"""Tests for the in-process AG-UI streaming path — the wrapper's only path since
core 1.0 (ADR 0003).

Guarded by importorskip as a safety net, but base deps pull the AG-UI runtime
(core's [agui] extra) so CI always runs these.
"""
import asyncio

import pytest

pytest.importorskip("ag_ui_langgraph")
pytest.importorskip("fastapi")

from langchain_core.messages import AIMessage  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.graph import END, START, MessagesState, StateGraph  # noqa: E402
from langgraph.types import interrupt  # noqa: E402
from langstage_core import load_agent_spec  # noqa: E402

from langstage_jupyter import agent_wrapper as aw  # noqa: E402
from langstage_jupyter.agui_stream import (  # noqa: E402
    agui_stream_updates,
    build_session_agent,
    stream_updates_sync,
)


def _collect(agent, msg, resume=None):
    async def go():
        return [c async for c in agui_stream_updates(agent, msg, "t", resume=resume)]

    return asyncio.run(go())


def test_text_parity_on_demo_stub():
    agent = build_session_agent(load_agent_spec("langstage_core.demo.stub:graph"))
    chunks = _collect(agent, "hello jupyter agui")
    text = "".join(c["chunk"] for c in chunks if "chunk" in c)
    assert "hello jupyter agui" in text
    assert chunks[-1]["status"] == "complete"


def test_sync_bridge_yields_chunks():
    """stream_updates_sync pumps the async generator in a plain (sync) context."""
    agent = build_session_agent(load_agent_spec("langstage_core.demo.stub:graph"))
    chunks = list(stream_updates_sync(agent, "sync bridge", "t"))
    # tokens stream across chunks, so join before checking (like the other tests)
    assert "sync bridge" in "".join(c.get("chunk", "") for c in chunks)
    assert chunks[-1]["status"] == "complete"


def _interrupt_graph():
    def gate(state):
        d = interrupt({"action_requests": [{"tool": "approve", "args": {}}]})
        return {"messages": [AIMessage(content=f"resolved: {d}")]}

    b = StateGraph(MessagesState)
    b.add_node("gate", gate)
    b.add_edge(START, "gate")
    b.add_edge("gate", END)
    return b.compile(checkpointer=InMemorySaver())


def test_interrupt_display_and_resume():
    agent = build_session_agent(_interrupt_graph())
    c1 = _collect(agent, "go")
    assert any(c.get("status") == "interrupt" for c in c1), c1
    c2 = _collect(agent, "", resume={"decisions": [{"type": "accept"}]})
    assert not any(c.get("status") == "interrupt" for c in c2), c2
    assert "resolved:" in "".join(c["chunk"] for c in c2 if "chunk" in c)


def test_wrapper_routes_through_agui():
    """AgentWrapper.execute() streams via the in-process AG-UI adapter (its only path)."""
    w = aw.AgentWrapper.__new__(aw.AgentWrapper)  # bypass __init__ (no agent to load)
    w.agent = load_agent_spec("langstage_core.demo.stub:graph")
    w._agui_agent = None

    chunks = list(w.execute(message="via wrapper", thread_id="w1"))
    text = "".join(c["chunk"] for c in chunks if "chunk" in c)
    assert "via wrapper" in text
    assert chunks[-1]["status"] == "complete"
