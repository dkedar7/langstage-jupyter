"""Configuration management for langstage-jupyter.

Shares the ``DEEPAGENT_*`` schema + TOML loader with the deep-agent family via
``langstage_core.host``. ``LabConfig`` is the full resolved config
(``defaults < deepagents.toml < DEEPAGENT_* env < overrides``) used by the
launcher and `--show-config`. The module-level constants below are an
env+defaults view (no TOML) kept for back-compat with existing call sites
(``agent.py``, ``agent_wrapper.py``).
"""
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar, Optional, cast

from langstage_core.host import HostConfig, load_toml_config  # noqa: F401  (re-exported for callers)


def get_config(key: str, default: Any = None, type_cast: Optional[Callable] = None) -> Any:
    """Get a config value from env, canonical ``LANGSTAGE_{KEY}`` first.

    Priority: ``LANGSTAGE_{KEY}`` > legacy ``DEEPAGENT_{KEY}`` > default. The
    canonical spelling must work — it's what ``--show-config`` advertises — so
    this no longer reads only the legacy name (gh #-dogfood).
    """
    canonical = os.getenv(f"LANGSTAGE_{key.upper()}")
    legacy = os.getenv(f"DEEPAGENT_{key.upper()}")
    env_value = canonical if canonical is not None else legacy
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

    @classmethod
    def resolve(
        cls,
        *,
        overrides: Optional[dict[str, Any]] = None,
        toml_start: Optional[Path] = None,
        env: Optional[dict[str, str]] = None,
        use_toml: bool = True,
    ) -> "LabConfig":
        """Resolve config, ignoring malformed numeric environment values."""
        clean_env = dict(os.environ if env is None else env)
        numeric_env = (
            ("LANGSTAGE_MODEL_TEMPERATURE", "DEEPAGENT_MODEL_TEMPERATURE"),
            ("LANGSTAGE_EXECUTE_TIMEOUT", "DEEPAGENT_EXECUTE_TIMEOUT"),
        )
        for canonical, legacy in numeric_env:
            used = canonical if clean_env.get(canonical) else legacy
            value = clean_env.get(used)
            if value in (None, ""):
                continue
            try:
                float(value)
            except (TypeError, ValueError):
                print(
                    f"note: ignoring malformed {used}={value!r}; "
                    "using TOML or default instead.",
                    file=sys.stderr,
                )
                clean_env.pop(canonical, None)
                clean_env.pop(legacy, None)
        return cast(
            "LabConfig",
            super().resolve(
                overrides=overrides,
                toml_start=toml_start,
                env=clean_env,
                use_toml=use_toml,
            ),
        )


# Module-level constants derived from LabConfig, for call sites that read
# ``config.X`` (agent.py, agent_wrapper.py). TOML is ON so these honor
# ``langstage.toml`` — the same resolution ``--show-config`` advertises.
# Previously this used ``use_toml=False``, so the default agent silently ignored
# langstage.toml while --show-config presented it as a live source (gh #-dogfood).
_cfg = LabConfig.resolve()

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
# Resolved through LabConfig so canonical LANGSTAGE_EXECUTE_TIMEOUT and
# jupyter.execute_timeout in langstage.toml both apply (the old get_config()
# read only DEEPAGENT_EXECUTE_TIMEOUT). agent.py reads this constant.
EXECUTE_TIMEOUT = _cfg.execute_timeout
