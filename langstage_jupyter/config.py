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


# The malformed-numeric-env handling that used to live here (a `_lenient_number`
# wrapper that returned the field default directly) is gone: it short-circuited config
# precedence (a bad env var clobbered a valid langstage.toml value and mislabeled the
# source), and langstage-core 1.0.23 (#104) now handles a malformed numeric env var in
# HostConfig.resolve() itself — catching the caster error, emitting the note, and
# keeping the value from the layer beneath env. The casters below are plain float/int
# and delegate to that. (gh #83)


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


# The base ``HostConfig.describe()`` footer for the no-file case. We match on this
# (via a stable prefix, below) to rewrite it when a file WAS found but couldn't be
# parsed — see ``LabConfig.describe`` and gh #86.
_ABSENT_TOML_FOOTER_PREFIX = "  TOML: no langstage.toml"


def _malformed_config_paths(toml_start: Optional[Path] = None) -> list[Path]:
    """Config files that exist on the resolved search path but failed to parse.

    langstage-core records every path whose TOML parse raised in the module-level
    ``_malformed_toml`` set (gh langstage-hermes #61/#42) and emits the one-line
    ``note: ignoring malformed config <path> ...`` on stderr from there. That set is
    process-global and accumulates across every ``resolve()`` in the process, so we
    intersect it with the files actually on THIS resolve's search path — the nearest
    project ``langstage.toml`` / legacy ``deepagents.toml`` and the global config — so a
    stale entry from an unrelated resolve elsewhere in the process can't leak in. Returns
    the found-but-malformed paths (usually zero or one), in global-then-project order.
    """
    from langstage_core.host import config as _core

    malformed = getattr(_core, "_malformed_toml", set())
    if not malformed:
        return []
    found: list[Path] = []
    gpath = _core._global_toml_path()
    if gpath.is_file() and str(gpath) in malformed:
        found.append(gpath)
    ppath = _core._find_project_toml(toml_start)
    if ppath is not None and str(ppath) in malformed:
        found.append(ppath)
    return found


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
        # Plain numeric casters: a malformed value is handled by the base
        # HostConfig.resolve(), which (since langstage-core 1.0.23, #104) catches the
        # caster error, emits a one-line note, and keeps the value from the layer
        # BENEATH env — a langstage.toml value if one is set, else the field default.
        # The old _lenient_number wrapper here returned the field default DIRECTLY,
        # which short-circuited that precedence: a malformed env var discarded a valid
        # langstage.toml value and mislabeled the source as env (gh #83). Delegating to
        # core fixes both, and de-duplicates the leniency into one place.
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
    def resolve(cls, *, toml_start: Optional[Path] = None, **kwargs: Any) -> "LabConfig":
        """Resolve config, additionally recording any found-but-malformed TOML.

        Delegates to ``HostConfig.resolve`` and then, while ``toml_start`` is still in
        hand, stashes the config files that exist on this resolve's search path but
        failed to parse (``_malformed_config_paths``). ``describe()`` reads that back so
        the ``--show-config`` footer can tell a present-but-unparseable ``langstage.toml``
        apart from a genuinely absent one (gh #86) — the base footer keys purely off the
        SUCCESSFULLY-parsed paths, so a malformed file collapses into "not found".
        """
        obj = super().resolve(toml_start=toml_start, **kwargs)
        use_toml = kwargs.get("use_toml", True)
        obj._malformed_toml_paths = (  # type: ignore[attr-defined]
            _malformed_config_paths(toml_start) if use_toml else []
        )
        return obj

    def describe(
        self,
        omit_keys: Optional[list] = None,
        configurable: Optional[dict] = None,
    ) -> str:
        """Base ``describe`` plus a truthful footer for a malformed ``langstage.toml``.

        The base ``HostConfig.describe`` footer says ``TOML: no langstage.toml ... found``
        whenever no file parsed successfully — which lumps a present-but-unparseable file
        in with a genuinely absent one, directly contradicting the ``note: ignoring
        malformed config <path> ...`` this same command already prints on stderr (gh #86).
        When a file WAS found on the search path but rejected as malformed, rewrite that
        footer to say so, pointing at the stderr note instead of at "not found". A valid
        file still shows its ``TOML read from:`` footer and genuine absence still shows
        ``no ... found`` — both untouched.
        """
        text = super().describe(omit_keys=omit_keys, configurable=configurable)
        malformed = getattr(self, "_malformed_toml_paths", [])
        if not malformed:
            return text
        new_footer = (
            "  TOML: found "
            + ", ".join(str(p) for p in malformed)
            + " but it is malformed (see the note above); using environment + defaults"
        )
        lines = text.split("\n")
        for i, line in enumerate(lines):
            # Only the no-file footer starts this way; a "TOML read from:" footer (some
            # file parsed) is left alone, so a mix of a valid global + malformed project
            # keeps crediting the file that DID load.
            if line.startswith(_ABSENT_TOML_FOOTER_PREFIX):
                lines[i] = new_footer
                break
        return "\n".join(lines)


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
