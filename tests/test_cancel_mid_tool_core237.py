"""Stop during a tool call, then the next turn works (core #237, adopted in 0.6.53).

A turn cancelled while a tool runs used to leave the model's tool call with no
result in the thread, and providers reject a history like that on the next turn.
langstage-core 1.0.62 answers such calls with a "cancelled" tool result before the
next turn starts; this checks that jupyter's stream path gets that behaviour.
"""

import asyncio
import threading

import pytest

pytest.importorskip("ag_ui_langgraph")

from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.graph import END, START, MessagesState, StateGraph  # noqa: E402

from langstage_jupyter.agui_stream import (  # noqa: E402
    CANCELLED_FRAME,
    build_session_agent,
    stream_updates_sync,
)


def _tool_graph():
    def model(state):
        msgs = state["messages"]
        answered = {m.tool_call_id for m in msgs if isinstance(m, ToolMessage)}
        for m in msgs:
            for call in getattr(m, "tool_calls", None) or []:
                if call["id"] not in answered:
                    # What a provider does with a dangling tool call.
                    raise ValueError(f"tool call {call['id']} has no result")
        if msgs[-1].type == "human" and msgs[-1].content == "first":
            return {"messages": [AIMessage(content="", tool_calls=[
                {"name": "slow", "args": {}, "id": "call-1", "type": "tool_call"}])]}
        return {"messages": [AIMessage(content="ok")]}

    async def tools(state):
        await asyncio.sleep(2.0)
        return {"messages": [ToolMessage(content="done", tool_call_id="call-1")]}

    def route(state):
        return "tools" if getattr(state["messages"][-1], "tool_calls", None) else END

    g = StateGraph(MessagesState)
    g.add_node("model", model)
    g.add_node("tools", tools)
    g.add_edge(START, "model")
    g.add_conditional_edges("model", route, ["tools", END])
    g.add_edge("tools", END)
    return g.compile(checkpointer=InMemorySaver())


def test_next_turn_succeeds_after_stop_during_a_tool():
    agent = build_session_agent(_tool_graph())
    cancel = threading.Event()
    threading.Timer(0.4, cancel.set).start()
    frames = list(stream_updates_sync(agent, "first", "t237", should_cancel=cancel.is_set))
    assert frames[-1] == CANCELLED_FRAME

    frames = list(stream_updates_sync(agent, "second", "t237", should_cancel=lambda: False))
    assert frames[-1]["status"] == "complete", frames[-1]

    state = asyncio.run(agent.graph.aget_state({"configurable": {"thread_id": "t237"}}))
    results = [m for m in state.values["messages"] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in results] == ["call-1"]
    assert "cancel" in str(results[0].content).lower()
