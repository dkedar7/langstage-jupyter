"""
Pytest configuration and shared fixtures for deepagent-lab tests.
"""
import os
import pytest
from pathlib import Path
from unittest.mock import Mock


@pytest.fixture
def clean_env(monkeypatch):
    """
    Fixture that provides a clean environment without DEEPAGENT_* variables.

    Usage:
        def test_something(clean_env):
            # All DEEPAGENT_* env vars are removed
            pass
    """
    # Remove all DEEPAGENT_* environment variables
    for key in list(os.environ.keys()):
        if key.startswith('DEEPAGENT_'):
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
