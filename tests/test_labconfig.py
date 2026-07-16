"""Tests for LabConfig — langstage-jupyter's HostConfig subclass (TOML-aware)."""
from pathlib import Path

import pytest

from langstage_jupyter.config import LabConfig


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Point the global deepagents config at an empty dir so the host machine's
    ~/.deepagents/config.toml can't leak in."""
    empty = tmp_path / "global"
    empty.mkdir()
    monkeypatch.setenv("DEEPAGENTS_CONFIG_HOME", str(empty))
    return tmp_path


def _toml(d: Path, body: str) -> None:
    (d / "deepagents.toml").write_text(body)


def test_defaults(isolated, tmp_path):
    cfg = LabConfig.resolve(env={}, toml_start=tmp_path)
    assert cfg.model_name == "anthropic:claude-sonnet-4-6"
    assert cfg.model_temperature == 0.0
    assert cfg.jupyter_token == "12345"
    assert cfg.jupyter_server_url == "http://localhost:8889"
    assert cfg.virtual_mode is True
    assert cfg.agent_module == "langstage_jupyter.agent"
    assert cfg.agent_variable is None
    assert cfg.execute_timeout == 300.0


def test_env_layer(isolated, tmp_path):
    cfg = LabConfig.resolve(
        env={
            "DEEPAGENT_MODEL_NAME": "openai:gpt-4",
            "DEEPAGENT_MODEL_TEMPERATURE": "0.7",
            "DEEPAGENT_VIRTUAL_MODE": "false",
            "DEEPAGENT_AGENT_SPEC": "x.py:g",
        },
        toml_start=tmp_path,
    )
    assert cfg.model_name == "openai:gpt-4"
    assert cfg.model_temperature == 0.7
    assert cfg.virtual_mode is False
    assert cfg.agent_spec == "x.py:g"


@pytest.mark.parametrize(
    ("env_var", "field", "default"),
    [
        ("LANGSTAGE_MODEL_TEMPERATURE", "model_temperature", 0.0),
        ("DEEPAGENT_MODEL_TEMPERATURE", "model_temperature", 0.0),
        ("LANGSTAGE_EXECUTE_TIMEOUT", "execute_timeout", 300.0),
        ("DEEPAGENT_EXECUTE_TIMEOUT", "execute_timeout", 300.0),
    ],
)
def test_malformed_numeric_env_uses_default(
    isolated, tmp_path, capsys, env_var, field, default
):
    cfg = LabConfig.resolve(env={env_var: "invalid"}, toml_start=tmp_path)

    assert getattr(cfg, field) == default
    assert env_var in capsys.readouterr().err


def test_toml_layer(isolated, tmp_path):
    _toml(tmp_path,
          '[model]\nname = "anthropic:claude-3"\ntemperature = 0.3\n'
          '[jupyter]\ntoken = "tok"\nvirtual_mode = false\nexecute_timeout = 60.0\n'
          '[agent]\nspec = "a.py:g"\nmodule = "custom.mod"\n')
    cfg = LabConfig.resolve(env={}, toml_start=tmp_path)
    assert cfg.model_name == "anthropic:claude-3"
    assert cfg.model_temperature == 0.3
    assert cfg.jupyter_token == "tok"
    assert cfg.virtual_mode is False
    assert cfg.execute_timeout == 60.0
    assert cfg.agent_spec == "a.py:g"
    assert cfg.agent_module == "custom.mod"


def test_env_beats_toml(isolated, tmp_path):
    _toml(tmp_path, '[model]\nname = "from-toml"\n')
    cfg = LabConfig.resolve(env={"DEEPAGENT_MODEL_NAME": "from-env"}, toml_start=tmp_path)
    assert cfg.model_name == "from-env"
    assert cfg.sources["model_name"] == "env:DEEPAGENT_MODEL_NAME"


def test_describe_lists_var_names(isolated, tmp_path):
    text = LabConfig.resolve(env={}, toml_start=tmp_path).describe()
    assert "DEEPAGENT_MODEL_NAME" in text
    assert "DEEPAGENT_JUPYTER_TOKEN" in text
    assert "jupyter.token" in text
    assert "DEEPAGENT_AGENT_SPEC" in text
