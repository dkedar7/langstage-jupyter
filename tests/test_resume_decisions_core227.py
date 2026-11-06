"""A refused HITL decision is a 400 and the approval stays answerable (core #227).

``ResumeHandler`` checks the decisions against the thread's pending approval with
``langstage_core.decision_problems`` before streaming. An answer
``HumanInTheLoopMiddleware`` would refuse (a type the tool doesn't allow) is never sent,
so the thread is still paused on the same approval and a correct answer goes through.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

pytest.importorskip("ag_ui_langgraph")
pytest.importorskip("langchain.agents.middleware")

from langchain.agents import create_agent  # noqa: E402
from langchain.agents.middleware import HumanInTheLoopMiddleware  # noqa: E402
from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from tornado.web import Application, HTTPError  # noqa: E402

from langstage_jupyter import handlers  # noqa: E402
from langstage_jupyter.agent_wrapper import AgentWrapper  # noqa: E402


@tool
def send_email(to: str) -> str:
    """Send an email."""
    return f"sent to {to}"


class _Model(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):
        if any(isinstance(m, ToolMessage) for m in messages):
            msg = AIMessage(content="Done.")
        else:
            msg = AIMessage(content="", tool_calls=[
                {"name": "send_email", "args": {"to": "a@example.org"}, "id": "c1"}])
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _wrapper():
    # Skip __init__'s agent loading; set exactly what execute() reads.
    w = AgentWrapper.__new__(AgentWrapper)
    w.agent = create_agent(_Model(), tools=[send_email], middleware=[
        HumanInTheLoopMiddleware(interrupt_on={"send_email": {"allowed_decisions": ["approve", "edit"]}}),
    ])
    w._agui_agent = None
    w.load_error = None
    return w


def _chunks(it):
    return list(it)


def _resume_handler(body, written):
    h = handlers.ResumeHandler(Application(), MagicMock())
    h._jupyter_current_user = "test"
    h._current_user = "test"
    h.get_json_body = lambda: body
    h.write = lambda data: written.append(data)

    async def _flush(*a, **k):
        return None

    h.flush = _flush
    h.finish = lambda *a, **k: None
    return h


def test_refused_decision_is_400_and_the_approval_can_be_answered(monkeypatch):
    w = _wrapper()
    first = _chunks(w.execute(message="email a", thread_id="t1"))
    assert any(c.get("interrupt") or c.get("status") == "interrupt" for c in first), first
    monkeypatch.setattr(handlers, "get_agent", lambda: w)

    written: list = []
    bad = _resume_handler({"thread_id": "t1", "decisions": [{"type": "reject"}]}, written)
    with pytest.raises(HTTPError) as exc:
        asyncio.run(bad.post())
    assert exc.value.status_code == 400
    assert "allowed: approve, edit" in exc.value.log_message
    assert written == []  # nothing streamed, nothing sent to the graph

    # Still paused on the same approval: approve goes through.
    done = _chunks(w.resume_from_interrupt([{"type": "approve"}], thread_id="t1"))
    assert not any(c.get("status") == "error" for c in done), done
    assert "Done." in str(done), done


def test_no_pending_approval_means_nothing_to_check():
    w = _wrapper()
    assert asyncio.run(w.decision_problems([{"type": "reject"}], "fresh")) == []
