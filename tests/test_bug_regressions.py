"""Regressions for the daily-routine bugs #23 and #24.

#23 — the shipped default agent name had a space ("Default Agent"), which leaks
into the LLM message `name` field and 400s on OpenAI-compatible providers on the
second turn. The default must be provider-safe; the upstream factory also
slugifies as a backstop (langgraph-stream-parser>=0.6.3).

#24 — `pip install langstage-jupyter` didn't pull in JupyterLab (it was only a
build dep), so the headline launcher (`jupyter lab`) failed on a fresh install,
and the launcher's "install jupyterlab" guard never fired (jupyter IS present;
only the jupyter-lab subcommand is missing). JupyterLab is now a runtime dep and
the launcher pre-checks the import.
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

# tomllib is stdlib on 3.11+ (this package requires >=3.11).
import tomllib

_REPO = Path(__file__).resolve().parent.parent
_AGENT_SRC = (_REPO / "langstage_jupyter" / "agent.py").read_text(encoding="utf-8")
_PYPROJECT = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))


def _shipped_default_agent_name() -> str:
    """The literal `name="..."` passed to the default-agent factory call."""
    start = _AGENT_SRC.index("_build_default_agent(")
    m = re.search(r"""\bname\s*=\s*["']([^"']*)["']""", _AGENT_SRC[start:])
    assert m, "no name= literal in the _build_default_agent(...) call"
    return m.group(1)


def test_default_agent_name_is_provider_safe():
    """#23: shipped default name must not contain spaces or <|\\/> (OpenAI 400)."""
    from langgraph_stream_parser.demo.agent import _safe_agent_name

    name = _shipped_default_agent_name()
    assert " " not in name, f"default agent name {name!r} contains a space"
    # The slugifier is a no-op on an already-safe name.
    assert _safe_agent_name(name) == name


def test_core_floor_carries_slugify_fix():
    """#23: the core dep floor must be >=0.6.3 (where the slugify fix landed)."""
    deps = _PYPROJECT["project"]["dependencies"]
    core = next(d for d in deps if d.lower().startswith("langgraph-stream-parser"))
    assert ">=0.6.3" in core, core


def test_jupyterlab_is_a_declared_runtime_dependency():
    """#24: JupyterLab must be a runtime dep, not build-only."""
    deps = _PYPROJECT["project"]["dependencies"]
    assert any(d.lower().startswith("jupyterlab") for d in deps), deps


class TestEnsureJupyterlabGuard:
    """#24: the launcher must fail fast with an actionable hint, not a help dump."""

    def test_exits_with_hint_when_jupyterlab_missing(self, monkeypatch, capsys):
        from langstage_jupyter import launcher

        real_find_spec = importlib.util.find_spec

        def fake_find_spec(name, *args, **kwargs):
            if name == "jupyterlab":
                return None  # simulate JupyterLab not installed
            return real_find_spec(name, *args, **kwargs)

        monkeypatch.setattr(launcher.importlib.util, "find_spec", fake_find_spec)

        with pytest.raises(SystemExit) as exc:
            launcher.ensure_jupyterlab()
        assert exc.value.code == 1
        assert "pip install jupyterlab" in capsys.readouterr().out

    def test_passes_when_jupyterlab_present(self):
        from langstage_jupyter import launcher

        # JupyterLab is a declared runtime dep, so it must import cleanly here.
        launcher.ensure_jupyterlab()  # must not raise
