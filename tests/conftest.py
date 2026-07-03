"""
Pytest configuration and shared fixtures for langstage-jupyter tests.
"""
import os
import pytest
from pathlib import Path
from unittest.mock import Mock


@pytest.fixture(autouse=True)
def _restore_workspace_root():
    """Snapshot + restore the process-global workspace state around each test.

    Two globals persist across tests under pytest (a real session is a fresh
    process): ``config.WORKSPACE_ROOT`` (read at wrapper init for the gh #45 pinned
    detection) and, since ADR 0005, ``langstage_core``'s active workspace + the
    ``LANGSTAGE_WORKSPACE_ROOT`` / ``DEEPAGENT_WORKSPACE_ROOT`` env vars that
    ``apply_workspace()`` publishes. Restoring all of them per test keeps isolation.
    """
    import langstage_jupyter.config as _cfg
    from langstage_core.host import workspace as _ws

    saved_root = _cfg.WORKSPACE_ROOT
    saved_active = _ws._ACTIVE
    saved_env = {k: os.environ.get(k) for k in ("LANGSTAGE_WORKSPACE_ROOT", "DEEPAGENT_WORKSPACE_ROOT")}
    try:
        yield
    finally:
        _cfg.WORKSPACE_ROOT = saved_root
        _ws._ACTIVE = saved_active
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture
def clean_env(monkeypatch):
    """
    Fixture that provides a clean environment without LANGSTAGE_*/DEEPAGENT_* vars.

    Must clear BOTH vocabularies: the canonical ``LANGSTAGE_*`` names take
    precedence over the legacy ``DEEPAGENT_*`` ones, so leaving a canonical name
    set (e.g. ``LANGSTAGE_WORKSPACE_ROOT`` published by a prior ``set_root_dir``
    call) would override the legacy value a test sets and silently corrupt
    config resolution.

    Usage:
        def test_something(clean_env):
            # All LANGSTAGE_*/DEEPAGENT_* env vars are removed
            pass
    """
    for key in list(os.environ.keys()):
        if key.startswith(('LANGSTAGE_', 'DEEPAGENT_')):
            monkeypatch.delenv(key, raising=False)
    return monkeypatch


@pytest.fixture
def mock_env(monkeypatch):
    """
    Fixture that provides helper to set environment variables.

    Usage:
        def test_something(mock_env):
            mock_env('DEEPAGENT_PORT', '9999')
    """
    def _set_env(key, value):
        monkeypatch.setenv(key, str(value))
    return _set_env


@pytest.fixture
def temp_workspace(tmp_path):
    """
    Fixture that creates a temporary workspace directory.

    Returns a Path object to the temporary directory.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def mock_langgraph_message():
    """
    Factory fixture for creating mock LangGraph message objects.

    Usage:
        def test_something(mock_langgraph_message):
            msg = mock_langgraph_message(content="Hello", role="user")
    """
    def _create_message(content=None, role="user", tool_calls=None, name=None):
        msg = Mock()
        msg.content = content
        msg.role = role
        msg.tool_calls = tool_calls or []
        msg.name = name
        msg.__class__.__name__ = "AIMessage" if role == "assistant" else "HumanMessage"
        return msg
    return _create_message


@pytest.fixture
def mock_tool_call():
    """
    Factory fixture for creating mock tool call objects.

    Usage:
        def test_something(mock_tool_call):
            tc = mock_tool_call(name="get_notebook_state", args={"path": "test.ipynb"})
    """
    def _create_tool_call(name, args=None, tool_call_id=None):
        tc = Mock()
        tc.name = name
        tc.args = args or {}
        tc.id = tool_call_id or f"call_{name}"
        return tc
    return _create_tool_call
