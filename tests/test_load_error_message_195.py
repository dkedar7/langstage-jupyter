"""A failed agent load answers chat with the real cause and the real settings (gh #195).

The chat error used to say "Please create agent.py with your LangGraph agent or set
JUPYTER_AGENT_PATH" — neither of which this package reads — and dropped the load error
that ``/health`` already showed.
"""

import langstage_jupyter.handlers as handlers
from langstage_jupyter.agent_wrapper import AGENT_SETTING_HINT, AgentWrapper, not_loaded_message


def _broken_wrapper(tmp_path, monkeypatch):
    broken = tmp_path / "broken.py"
    broken.write_text('raise RuntimeError("boom at import")\n', encoding="utf-8")
    monkeypatch.delenv("LANGSTAGE_AGENT_SPEC", raising=False)
    monkeypatch.delenv("DEEPAGENT_AGENT_SPEC", raising=False)
    return AgentWrapper(agent_module_path=str(broken), agent_variable_name="graph")


def test_chat_error_carries_the_load_error(tmp_path, monkeypatch):
    w = _broken_wrapper(tmp_path, monkeypatch)
    assert w.agent is None and "boom at import" in w.load_error

    chunks = list(w.stream("hi", thread_id="t1"))
    assert len(chunks) == 1 and chunks[0]["status"] == "error"
    error = chunks[0]["error"]
    assert "boom at import" in error
    assert "LANGSTAGE_AGENT_SPEC" in error and "agent.spec" in error
    assert "Reload agent" in error


def test_chat_error_names_no_unread_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("JUPYTER_AGENT_PATH", "/somewhere/agent.py")
    w = _broken_wrapper(tmp_path, monkeypatch)
    error = next(iter(w.stream("hi")))["error"]
    assert "JUPYTER_AGENT_PATH" not in error
    assert "agent.py" not in error.replace("broken.py", "")


def test_resume_without_agent_gets_the_same_message(tmp_path, monkeypatch):
    w = _broken_wrapper(tmp_path, monkeypatch)
    error = next(iter(w.resume_from_interrupt([{"type": "approve"}])))["error"]
    assert error == not_loaded_message(w.load_error)


def test_message_without_a_recorded_cause():
    msg = not_loaded_message(None)
    assert msg.startswith("Agent not loaded.")
    assert AGENT_SETTING_HINT in msg and "JUPYTER_AGENT_PATH" not in msg


def test_health_fallback_names_real_settings(monkeypatch):
    monkeypatch.setattr(
        handlers, "get_agent", lambda: type("W", (), {"agent": None, "load_error": None})()
    )
    status, ready, message = handlers._agent_readiness()
    assert (status, ready) == ("agent_not_loaded", False)
    assert AGENT_SETTING_HINT in message
