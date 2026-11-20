"""The sidebar saves open notebooks before a turn (gh #207).

The save itself happens in the browser (``src/save.ts``, covered by the UI tests). The
server side is the switch: ``jupyter.save_before_turn`` in ``langstage.toml`` or
``LANGSTAGE_SAVE_BEFORE_TURN``, on by default, which ``/health`` reports to the sidebar.
"""

import asyncio
import json
import types

import pytest

import langstage_jupyter.handlers as handlers
from langstage_jupyter.config import LabConfig


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for var in ("LANGSTAGE_SAVE_BEFORE_TURN", "DEEPAGENT_SAVE_BEFORE_TURN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_on_by_default(isolated):
    cfg = LabConfig.resolve(env={}, toml_start=isolated)
    assert cfg.save_before_turn is True
    assert cfg.sources["save_before_turn"] == "default"


def test_turned_off_in_langstage_toml(isolated):
    (isolated / "langstage.toml").write_text("[jupyter]\nsave_before_turn = false\n")
    cfg = LabConfig.resolve(env={}, toml_start=isolated)
    assert cfg.save_before_turn is False
    assert cfg.sources["save_before_turn"].startswith("toml")


@pytest.mark.parametrize("var", ["LANGSTAGE_SAVE_BEFORE_TURN", "DEEPAGENT_SAVE_BEFORE_TURN"])
def test_turned_off_by_env(isolated, var):
    cfg = LabConfig.resolve(env={var: "false"}, toml_start=isolated)
    assert cfg.save_before_turn is False


def test_an_unrecognized_value_keeps_it_on(isolated, capsys):
    cfg = LabConfig.resolve(env={"LANGSTAGE_SAVE_BEFORE_TURN": "sometimes"}, toml_start=isolated)
    assert cfg.save_before_turn is True
    assert "SAVE_BEFORE_TURN" in capsys.readouterr().err


class _Graph:
    name = "Stub"

    async def astream(self, *a, **k):
        yield {}


def _health_body(monkeypatch, save_before_turn):
    from langstage_jupyter import config

    monkeypatch.setattr(config, "SAVE_BEFORE_TURN", save_before_turn, raising=False)
    monkeypatch.setattr(handlers, "get_agent", lambda: types.SimpleNamespace(agent=_Graph()))
    monkeypatch.setattr(handlers, "_agent_readiness", lambda: ("healthy", True, "Agent is ready"))
    sent = []
    fake = types.SimpleNamespace(
        settings={"server_root_dir": None},
        finish=sent.append,
        log=types.SimpleNamespace(error=lambda *a, **k: None),
    )
    # Skip @tornado.web.authenticated: this checks the body, not the auth.
    asyncio.run(handlers.HealthHandler.get.__wrapped__(fake))
    return json.loads(sent[0])


@pytest.mark.parametrize("value", [True, False])
def test_health_tells_the_sidebar(monkeypatch, value):
    body = _health_body(monkeypatch, value)
    assert body["save_before_turn"] is value
    assert body["ready"] is True
