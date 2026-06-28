"""Workspace re-rooting actually re-roots the agent now (gh #36).

`set_root_dir` ran on every chat message but its backend-rebuild branch was dead:
it imported the non-existent `deepagents.tools.filesystem` behind a
`hasattr(agent, 'backend')` guard that's never true for a CompiledStateGraph. So
the documented "automatic workspace discovery" never happened. These tests cover
the control flow: env is always published, the agent is rebuilt only when the
resolved root changes, and an agent that *does* expose a mutable backend is
re-pointed in place via the corrected import.
"""

from pathlib import Path

from langstage_jupyter.agent_wrapper import AgentWrapper


def _wrapper(applied_root, agent):
    """Build a wrapper without loading a real agent (no model wiring needed)."""
    w = AgentWrapper.__new__(AgentWrapper)
    w.agent = agent
    w.agent_module_path = "langstage_jupyter.agent"
    w.agent_variable_name = None
    w._applied_root = AgentWrapper._resolve_root(applied_root)
    return w


def test_publishes_env_vars(monkeypatch, tmp_path):
    monkeypatch.delenv("LANGSTAGE_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("DEEPAGENT_WORKSPACE_ROOT", raising=False)
    w = _wrapper(".", object())
    w.reload_agent = lambda: None  # don't actually rebuild
    w.set_root_dir(str(tmp_path))
    import os
    assert os.environ["LANGSTAGE_WORKSPACE_ROOT"] == str(tmp_path)
    assert os.environ["DEEPAGENT_WORKSPACE_ROOT"] == str(tmp_path)  # legacy still set


def test_noop_when_root_unchanged(tmp_path):
    # An agent with no mutable backend, already rooted at tmp_path.
    w = _wrapper(str(tmp_path), object())
    calls = []
    w.reload_agent = lambda: calls.append(1)
    # Same resolved root (even via a non-normalized spelling) => no rebuild.
    w.set_root_dir(str(tmp_path) + "/.")
    assert calls == [], "must not rebuild when the root is unchanged"


def test_rebuilds_when_root_changes(tmp_path):
    w = _wrapper(str(tmp_path / "old"), object())  # no .backend
    calls = []
    w.reload_agent = lambda: calls.append(1)
    new_root = tmp_path / "new"
    new_root.mkdir()
    w.set_root_dir(str(new_root))
    assert calls == [1], "must rebuild once when the root changes"
    assert w._applied_root == AgentWrapper._resolve_root(str(new_root))


def test_in_place_backend_swap_uses_corrected_import(tmp_path):
    # An agent that exposes a mutable `.backend` is re-pointed in place (no rebuild).
    class FakeAgent:
        backend = None

    agent = FakeAgent()
    w = _wrapper(str(tmp_path / "old"), agent)
    calls = []
    w.reload_agent = lambda: calls.append(1)
    new_root = tmp_path / "new"
    new_root.mkdir()
    w.set_root_dir(str(new_root))
    assert calls == [], "in-place swap should not trigger a rebuild"
    # The corrected import (deepagents.backends) actually produced a backend.
    from deepagents.backends import FilesystemBackend
    assert isinstance(agent.backend, FilesystemBackend)


def test_resolve_root_normalizes():
    assert AgentWrapper._resolve_root(".") == str(Path(".").resolve())
