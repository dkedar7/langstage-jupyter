"""Regressions for adopting langstage-core 1.0.36 in place of local duplicates.

- gh #151: a colon-less / malformed agent spec is a clear error from core's
  ``parse_agent_spec``, on every path (-a, env spec, --verify, --ask, --serve-check,
  /health), never a silent fallback to the bundled default agent.
- gh #133: a quoted TOML boolean (``virtual_mode = "false"``) is coerced by core's
  ``_coerce`` with the env-bool rules, instead of a non-empty string reading as truthy.
- gh #136: the missing-provider-key preflight infers the provider from a bare model
  name (``claude-*``, ``gpt-*``) the way ``init_chat_model`` does, so ``/health`` and
  ``--verify`` / ``--ask`` no longer skip the check for a prefix-less
  ``LANGSTAGE_MODEL_NAME``.
- The launcher prints through core's ``langstage_core.console.safe_print``.
"""
import re
import tomllib
import types
from pathlib import Path

import pytest

from langstage_jupyter import handlers
from langstage_jupyter.config import LabConfig
from langstage_jupyter.launcher import main

_REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """No host-machine global config, and no agent/model selection leaking in."""
    empty = tmp_path / "global"
    empty.mkdir()
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(empty))
    for var in (
        "LANGSTAGE_AGENT_SPEC", "DEEPAGENT_AGENT_SPEC",
        "LANGSTAGE_MODEL_NAME", "DEEPAGENT_MODEL_NAME",
        "LANGSTAGE_AGENT_MODULE", "DEEPAGENT_AGENT_MODULE",
        "LANGSTAGE_AGENT_VARIABLE", "DEEPAGENT_AGENT_VARIABLE",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _core_must_not_run(monkeypatch):
    """A regression that fell back to the default agent would reach core.verify."""
    monkeypatch.setattr(
        "langstage_core.agui.verify",
        lambda *a, **k: pytest.fail("no agent may be verified for a malformed spec"),
    )


def test_core_floor_is_1_0_36():
    deps = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    core = next(d for d in deps["project"]["dependencies"] if d.startswith("langstage-core"))
    m = re.search(r">=\s*(\d+)\.(\d+)\.(\d+)", core)
    assert m and tuple(map(int, m.groups())) >= (1, 0, 36), core


# ── gh #151 ──────────────────────────────────────────────────────────────────


class TestMalformedSpecIsAnError:
    def test_colon_less_dash_a_fails_up_front(self, isolated, monkeypatch, capsys):
        _core_must_not_run(monkeypatch)
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "-a", "my_agent.py", "--verify"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Invalid agent spec 'my_agent.py'" in err
        assert "my_agent.py:graph" in err  # core's fix-it hint

    def test_colon_less_env_spec_fails_verify(self, isolated, monkeypatch, capsys):
        _core_must_not_run(monkeypatch)
        monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", "my_agent.py")
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--verify"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "[fail] could not load agent: Invalid agent spec" in out
        # The old fallback preflighted the bundled default, which asks for its key.
        assert "ANTHROPIC_API_KEY" not in out

    def test_colon_less_env_spec_fails_ask(self, isolated, monkeypatch, capsys):
        monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", "my_agent.py")
        monkeypatch.setattr(
            "langstage_core.agui.collect_chunk_frames",
            lambda *a, **k: pytest.fail("no turn may run for a malformed spec"),
        )
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--ask", "hi"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        assert "Invalid agent spec" in capsys.readouterr().err

    def test_colon_less_env_spec_fails_serve_check(self, isolated, monkeypatch, capsys):
        monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", "my_agent.py")
        monkeypatch.setattr(
            "langstage_jupyter.launcher.serve_check",
            lambda *a, **k: pytest.fail("serve-check must not boot for a malformed spec"),
        )
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--serve-check"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        assert "Invalid agent spec" in capsys.readouterr().out

    def test_health_names_the_spec_error(self, monkeypatch):
        wrapper = types.SimpleNamespace(
            agent=None, load_error="Invalid agent spec 'my_agent.py'. ..."
        )
        monkeypatch.setattr(handlers, "get_agent", lambda: wrapper)
        status, ready, message = handlers._agent_readiness()
        assert (status, ready) == ("agent_not_loaded", False)
        assert "Invalid agent spec 'my_agent.py'" in message


# ── gh #133 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "literal, expected",
    [('"false"', False), ('"no"', False), ('"0"', False), ('"true"', True), ("false", False)],
)
def test_quoted_toml_bool_is_coerced(isolated, literal, expected):
    """gh #133: `virtual_mode = "false"` must turn the sandbox OFF, not read as truthy."""
    (isolated / "langstage.toml").write_text(f"[jupyter]\nvirtual_mode = {literal}\n")
    cfg = LabConfig.resolve(env={}, toml_start=isolated)
    assert cfg.virtual_mode is expected
    assert cfg.sources["virtual_mode"].startswith("toml")


def test_unrecognized_quoted_toml_bool_keeps_the_safe_default(isolated, capsys):
    """An unrecognized string degrades to the default (sandbox ON) with a note."""
    (isolated / "langstage.toml").write_text('[jupyter]\nvirtual_mode = "enabled"\n')
    cfg = LabConfig.resolve(env={}, toml_start=isolated)
    assert cfg.virtual_mode is True
    assert "virtual_mode" in capsys.readouterr().err


# ── gh #136 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model, key",
    [
        ("claude-sonnet-4-5", "ANTHROPIC_API_KEY"),
        ("gpt-4o", "OPENAI_API_KEY"),
        ("o3-mini", "OPENAI_API_KEY"),
        ("anthropic:claude-sonnet-4-6", "ANTHROPIC_API_KEY"),
        ("openai:gpt-4o", "OPENAI_API_KEY"),
    ],
)
def test_missing_key_is_detected_with_or_without_a_prefix(monkeypatch, model, key):
    monkeypatch.delenv(key, raising=False)
    assert handlers._missing_provider_key(model) == key
    monkeypatch.setenv(key, "sk-test")
    assert handlers._missing_provider_key(model) is None


@pytest.mark.parametrize("model", ["", "llama3", "ollama:llama3", "my-local-model"])
def test_unknown_provider_is_not_gated(model):
    assert handlers._missing_provider_key(model) is None


def test_health_is_not_green_for_a_bare_model_name_without_its_key(monkeypatch):
    """gh #136's repro: bundled default + bare `claude-*` name + no key -> needs_setup."""
    from langstage_jupyter import config

    class _Runnable:
        async def astream(self, *a, **k):
            yield {}

    monkeypatch.setattr(
        config, "_cfg",
        types.SimpleNamespace(
            agent_spec=None,
            sources={"agent_module": "default", "agent_variable": "default"},
        ),
        raising=False,
    )
    monkeypatch.setattr(config, "MODEL_NAME", "claude-sonnet-4-5", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(handlers, "get_agent", lambda: types.SimpleNamespace(agent=_Runnable()))
    status, ready, message = handlers._agent_readiness()
    assert (status, ready) == ("needs_setup", False)
    assert "ANTHROPIC_API_KEY" in message


def test_verify_names_the_key_for_a_bare_model_name(isolated, monkeypatch, capsys):
    """gh #136: --verify gives the clean key message, not a raw provider error."""
    monkeypatch.setenv("LANGSTAGE_MODEL_NAME", "claude-sonnet-4-5")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        "langstage_core.agui.verify",
        lambda *a, **k: pytest.fail("core.verify must not run when the key is missing"),
    )
    monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--verify"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().out


# ── console output ───────────────────────────────────────────────────────────


def test_launcher_prints_through_core_safe_print():
    """The local `_print` copy is gone; the launcher uses core's helper."""
    from langstage_core.console import safe_print

    from langstage_jupyter import launcher

    assert launcher.safe_print is safe_print
    assert not hasattr(launcher, "_print")
