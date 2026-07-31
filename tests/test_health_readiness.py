"""Sidebar readiness reflects whether the agent can run a turn, not just loaded (gh #60).

`HealthHandler` reported `agent_loaded = agent is not None`, so the bundled default agent
with no `ANTHROPIC_API_KEY` (a common first-run slip) showed 🟢 "ready" and then failed the
first turn with a provider auth error. Readiness now gates on runnability + a cheap
credential preflight for the default agent's provider, surfacing a distinct `needs_setup`
state (amber) with an actionable message.
"""

import types

import langstage_jupyter.handlers as handlers


class _RunnableGraph:
    async def astream(self, *a, **k):  # a compiled graph has astream
        yield {}

    name = "default-agent"


class _Uncompiled:
    def compile(self):  # a StateGraph builder: has compile, no astream
        pass


def _use_agent(monkeypatch, obj):
    monkeypatch.setattr(handlers, "get_agent", lambda: type("W", (), {"agent": obj})())


def _set_cfg(monkeypatch, *, agent_spec=None, module_source="default", variable_source="default"):
    """Point config._cfg at a synthetic resolved config.

    Readiness scopes its default-agent key check through config.is_bundled_default(config._cfg)
    — no spec AND agent_module/variable both from the ``default`` source — the same predicate
    --verify uses (gh #90/#94). Drive that predicate by faking _cfg's agent_spec + sources.
    """
    from langstage_jupyter import config

    cfg = types.SimpleNamespace(
        agent_spec=agent_spec,
        sources={"agent_module": module_source, "agent_variable": variable_source},
    )
    monkeypatch.setattr(config, "_cfg", cfg, raising=False)


def _default_anthropic(monkeypatch):
    from langstage_jupyter import config

    _set_cfg(monkeypatch)  # bundled default agent: no spec, module/variable both "default"
    monkeypatch.setattr(config, "MODEL_NAME", "anthropic:claude-sonnet-4-6", raising=False)


def test_default_agent_without_key_is_needs_setup(monkeypatch):
    _default_anthropic(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _use_agent(monkeypatch, _RunnableGraph())

    status, ready, message = handlers._agent_readiness()
    assert status == "needs_setup"
    assert ready is False
    assert "ANTHROPIC_API_KEY" in message


def test_default_agent_with_key_is_healthy(monkeypatch):
    _default_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    _use_agent(monkeypatch, _RunnableGraph())

    status, ready, _ = handlers._agent_readiness()
    assert status == "healthy" and ready is True


def test_uncompiled_graph_is_not_runnable(monkeypatch):
    _default_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")  # key present, but graph unrunnable
    _use_agent(monkeypatch, _Uncompiled())

    status, ready, message = handlers._agent_readiness()
    assert status == "not_runnable" and ready is False
    assert "runnable" in message.lower()


def test_custom_agent_skips_the_default_key_check(monkeypatch):
    # A BYO agent's credentials are the operator's concern — no default-key preflight.
    _set_cfg(monkeypatch, agent_spec="my_agent.py:graph")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _use_agent(monkeypatch, _RunnableGraph())

    status, ready, _ = handlers._agent_readiness()
    assert status == "healthy" and ready is True


def test_keyless_module_variable_agent_is_ready(monkeypatch):
    # gh #94: a keyless CUSTOM agent selected via LANGSTAGE_AGENT_MODULE +
    # LANGSTAGE_AGENT_VARIABLE (no agent_spec, so both sources are "env", not "default")
    # must report READY — /health must NOT demand ANTHROPIC_API_KEY for it, the same way
    # its sibling --verify passes on the identical config (gh #90). Before the fix,
    # _missing_default_agent_key keyed off AGENT_SPEC alone: an empty spec fell through to
    # the bundled default's key check and reported the working custom agent as needs_setup.
    _set_cfg(monkeypatch, module_source="env", variable_source="env")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _use_agent(monkeypatch, _RunnableGraph())

    status, ready, message = handlers._agent_readiness()
    assert status == "healthy" and ready is True
    assert "ANTHROPIC_API_KEY" not in message  # not the bundled default's key check


def test_not_loaded_reports_agent_not_loaded(monkeypatch):
    _use_agent(monkeypatch, None)
    status, ready, _ = handlers._agent_readiness()
    assert status == "agent_not_loaded" and ready is False
