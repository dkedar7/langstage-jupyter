"""Configuration management for langstage-jupyter.

Shares the ``DEEPAGENT_*`` schema + TOML loader with the deep-agent family via
``langgraph_stream_parser.host``. ``LabConfig`` is the full resolved config
(``defaults < deepagents.toml < DEEPAGENT_* env < overrides``) used by the
launcher and `--show-config`. The module-level constants below are an
env+defaults view (no TOML) kept for back-compat with existing call sites
(``agent.py``, ``agent_wrapper.py``).
"""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar, Optional

from langgraph_stream_parser.host import HostConfig, load_toml_config  # noqa: F401  (re-exported for callers)


def get_config(key: str, default: Any = None, type_cast: Optional[Callable] = None) -> Any:
    """Get a config value from a ``DEEPAGENT_{KEY}`` env var (env-only helper).

    Priority: env var ``DEEPAGENT_{KEY}`` > default. Retained for ad-hoc keys
    (e.g. ``execute_timeout``) and back-compat.
    """
    env_key = f"DEEPAGENT_{key.upper()}"
    env_value = os.getenv(env_key)
    if env_value is not None:
        return type_cast(env_value) if type_cast else env_value
    return default


def _to_bool(value: str) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes", "on")


@dataclass
class LabConfig(HostConfig):
    """langstage-jupyter's view of the shared config.

    Adds the Jupyter / model / agent-loading keys on top of ``HostConfig``,
    resolved through the same ``defaults < deepagents.toml < DEEPAGENT_* env <
    overrides`` chain. (``DEEPAGENT_AGENT_SPEC`` is already canonical here.)
    """

    agent_module: str = "langstage_jupyter.agent"
    agent_variable: Optional[str] = None
    jupyter_token: str = "12345"
    jupyter_server_url: str = "http://localhost:8889"
    model_name: str = "anthropic:claude-sonnet-4-6"
    model_temperature: float = 0.0
    virtual_mode: bool = True
    execute_timeout: float = 300.0

    _ENV: ClassVar[dict] = {
        "agent_module": ("DEEPAGENT_AGENT_MODULE", str),
        "agent_variable": ("DEEPAGENT_AGENT_VARIABLE", str),
        "jupyter_token": ("DEEPAGENT_JUPYTER_TOKEN", str),
        "jupyter_server_url": ("DEEPAGENT_JUPYTER_SERVER_URL", str),
        "model_name": ("DEEPAGENT_MODEL_NAME", str),
        "model_temperature": ("DEEPAGENT_MODEL_TEMPERATURE", float),
        "virtual_mode": ("DEEPAGENT_VIRTUAL_MODE", _to_bool),
        "execute_timeout": ("DEEPAGENT_EXECUTE_TIMEOUT", float),
    }
    _TOML: ClassVar[dict] = {
        "agent_module": "agent.module",
        "agent_variable": "agent.variable",
        "jupyter_token": "jupyter.token",
        "jupyter_server_url": "jupyter.server_url",
        "model_name": "model.name",
        "model_temperature": "model.temperature",
        "virtual_mode": "jupyter.virtual_mode",
        "execute_timeout": "jupyter.execute_timeout",
    }


# Module-level constants — an env + defaults view (no TOML) derived from
# LabConfig, for back-compat with call sites that read ``config.X``. Full
# TOML-aware resolution is ``LabConfig.resolve()`` (used by the launcher).
_cfg = LabConfig.resolve(use_toml=False)

WORKSPACE_ROOT: Optional[Path] = (
    _cfg.workspace_root.resolve()
    if _cfg.sources.get("workspace_root") != "default"
    else None
)
AGENT_SPEC = _cfg.agent_spec
AGENT_MODULE = _cfg.agent_module
AGENT_VARIABLE = _cfg.agent_variable
JUPYTER_TOKEN = _cfg.jupyter_token
JUPYTER_SERVER_URL = _cfg.jupyter_server_url
MODEL_NAME = _cfg.model_name
MODEL_TEMPERATURE = _cfg.model_temperature
DEBUG = _cfg.debug
VIRTUAL_MODE = _cfg.virtual_mode
