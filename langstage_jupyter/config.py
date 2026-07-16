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
from typing import Any, Callable, ClassVar, Optional

from langstage_core.host import HostConfig, load_toml_config  # noqa: F401  (re-exported for callers)


# Malformed numeric env values already noted — so a bad value is flagged ONCE even
# though several surfaces each resolve the config in one process: the import-time
# ``_cfg`` below, plus ``--show-config`` / ``--verify`` / the launcher, which each
# call ``resolve()`` again. Mirrors langstage_core's ``_warned_legacy_env`` /
# ``_warned_malformed_toml`` dedupe so one typo doesn't spam identical notes.
_warned_malformed_env: set = set()


def _lenient_number(cast: Callable[[str], Any], canonical_var: str, default: Any) -> Callable[[str], Any]:
    """Wrap a numeric env caster (``float``/``int``) so a malformed value degrades
    to the default with a one-line stderr note instead of crashing every entrypoint.

    Mirrors #42's malformed-TOML handling. Because ``LabConfig.resolve()`` runs at
    import time (``_cfg`` below), a bad numeric env value — ``LANGSTAGE_MODEL_TEMPERATURE``
    or ``LANGSTAGE_EXECUTE_TIMEOUT`` (or their legacy ``DEEPAGENT_*`` spellings) set to
    e.g. ``"abc"`` or ``"0,5"`` — used to let a raw ``ValueError`` from ``caster(ev)``
    escape and take down ``--version``/``--help``/``--show-config``, the launcher, the
    server extension, and even a bare ``import langstage_jupyter`` (gh #75). Now, exactly
    like #42's ``note: ignoring malformed config …; using environment + defaults instead``
    for a broken ``langstage.toml``, we skip the bad value, name the offending variable
    and the raw value it choked on (an improvement on #42's file-only message), and fall
    back to the field default so every entrypoint stays alive. ``canonical_var`` is the
    ``LANGSTAGE_*`` name ``--show-config`` advertises; the legacy ``DEEPAGENT_*`` spelling
    resolves through the same caster.
    """

    def _cast(value: str) -> Any:
        try:
            return cast(value)
        except (ValueError, TypeError) as exc:
            key = (canonical_var, value)
            if key not in _warned_malformed_env:
                _warned_malformed_env.add(key)
                print(
                    f"note: ignoring malformed {canonical_var}={value!r} "
                    f"({type(exc).__name__}: {exc}); using default {default!r} instead.",
                    file=sys.stderr,
                )
            return default

    return _cast


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
        # Lenient numeric casters: a malformed value degrades to the field default
        # with a stderr note (mirroring #42's malformed-TOML path) instead of letting
        # a raw ValueError crash every entrypoint at import time (gh #75). Defaults are
        # referenced from the fields above so the fallback can't drift from them.
        "model_temperature": (
            "DEEPAGENT_MODEL_TEMPERATURE",
            _lenient_number(float, "LANGSTAGE_MODEL_TEMPERATURE", model_temperature),
        ),
        "virtual_mode": ("DEEPAGENT_VIRTUAL_MODE", _to_bool),
        "execute_timeout": (
            "DEEPAGENT_EXECUTE_TIMEOUT",
            _lenient_number(float, "LANGSTAGE_EXECUTE_TIMEOUT", execute_timeout),
        ),
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
