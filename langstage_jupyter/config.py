"""Configuration management for langstage-jupyter.

Shares the ``DEEPAGENT_*`` schema + TOML loader with the deep-agent family via
``langstage_core.host``. ``LabConfig`` is the full resolved config
(``defaults < deepagents.toml < DEEPAGENT_* env < overrides``) used by the
launcher and `--show-config`. The module-level constants below are an
env+defaults view (no TOML) kept for back-compat with existing call sites
(``agent.py``, ``agent_wrapper.py``).
"""
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar, Optional

from langstage_core.host import HostConfig, load_toml_config  # noqa: F401  (re-exported for callers)
from langstage_core.host.config import _env_bool_strict


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


def _mask_secret(value: Any) -> str:
    """Redact a secret for display in ``--show-config``.

    Reveals enough to confirm the value is SET — and, for a token long enough that a
    last-4 fingerprint is meaningful, *which* one — without printing the secret itself.
    Short values (< 8 chars, e.g. the ``12345`` default) are fully starred so nothing
    material leaks. Mirrors the launcher startup banner, which already masks the token
    rather than printing it. (gh #105)
    """
    s = "" if value is None else str(value)
    if len(s) < 8:
        return "*" * len(s)
    return "****" + s[-4:]


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
    # Must match the launcher's own port scan (find_available_port(start_port=8888)),
    # the README's manual-config walkthrough (jupyter lab --port 8888), and every
    # --check-connection example. The old :8889 default disagreed with all three, so a
    # user relying on the default (or copying .env.example) got --check-connection
    # false-failing against a Jupyter that was actually up at :8888 (gh #99).
    jupyter_server_url: str = "http://localhost:8888"
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
        # Core's STRICT boolean caster, the one its own `debug` field uses: an
        # unrecognized value raises, so resolve() ignores it with a note and keeps the
        # layer beneath env. The old local lenient caster read ANY string outside
        # true/1/yes/on as False, so `LANGSTAGE_VIRTUAL_MODE=enabled` (an attempt to
        # turn the sandbox ON) silently turned it OFF, credited as [env] (gh #134).
        "virtual_mode": ("DEEPAGENT_VIRTUAL_MODE", _env_bool_strict),
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

    # Fields whose resolved value is a secret and must never be printed verbatim by
    # the config diagnostics. ``--show-config`` may SHOW jupyter_token (so the manual-
    # config flow is verifiable, gh #105), but only masked — see ``_mask_secret``.
    _SECRET_FIELDS: ClassVar[tuple] = ("jupyter_token",)

    @contextmanager
    def _secrets_masked_for_render(self, omit_keys: Optional[list]):
        """Temporarily replace secret fields with a masked fingerprint while the base
        ``describe`` / ``config_dict`` renders them, then restore the live values.

        The base renderers read ``getattr(self, field)`` for the value column; swapping
        the attribute for the duration of the call is the least invasive way to mask a
        secret in BOTH the human table and the ``--json`` twin without duplicating their
        formatting. A field that's omitted for this render is left untouched (it isn't
        printed anyway). Source attribution lives in a separate ``_sources`` map, so the
        ``[env:…]`` / ``[toml …]`` label a user needs to confirm precedence is unaffected.
        """
        omit = set(omit_keys or ())
        saved: dict[str, Any] = {}
        try:
            for name in self._SECRET_FIELDS:
                if name in omit:
                    continue
                saved[name] = getattr(self, name)
                setattr(self, name, _mask_secret(saved[name]))
            yield
        finally:
            for name, value in saved.items():
                setattr(self, name, value)

    # A present-but-malformed langstage.toml is reported by langstage-core itself since
    # 1.0.36 (the describe() footer says MALFORMED, config_dict()["toml"] carries
    # found/malformed/malformed_files), so the local footer rewrite + JSON override that
    # used to live here (gh #86, #88) are gone. These overrides only mask secrets.
    def describe(
        self,
        omit_keys: Optional[list] = None,
        configurable: Optional[dict] = None,
    ) -> str:
        """Base ``describe`` with secret fields masked (gh #105)."""
        with self._secrets_masked_for_render(omit_keys):
            return super().describe(omit_keys=omit_keys, configurable=configurable)

    def config_dict(
        self,
        omit_keys: Optional[list] = None,
        configurable: Optional[dict] = None,
    ) -> dict:
        """Base ``config_dict`` with secret fields masked (gh #105)."""
        with self._secrets_masked_for_render(omit_keys):
            return super().config_dict(omit_keys=omit_keys, configurable=configurable)


def is_bundled_default(cfg: "LabConfig") -> bool:
    """True when NO agent was configured at all, so the bundled default agent runs.

    The single definition of "the bundled default is in play" — no ``agent_spec`` AND
    both ``agent_module`` and ``agent_variable`` still coming from the ``default``
    source. This is the only case where the cheap credential preflight may demand the
    default model's provider key; a custom/BYO agent selected via ``agent_spec`` OR via
    ``agent_module`` + ``agent_variable`` is the operator's concern and must not be gated
    on the default agent's key.

    Shared by the launcher's ``--verify`` preflight and ``/health`` readiness so the two
    surfaces can't disagree about the identical config — keying this off ``agent_spec``
    alone is exactly what made both wrongly demand ``ANTHROPIC_API_KEY`` for a keyless
    module+variable agent (gh #90 for ``--verify``, gh #94 for ``/health``). Pure over the
    passed ``cfg`` (no module reads) so ``--verify`` can drive it off a live
    ``LabConfig.resolve()`` while ``/health`` drives it off the frozen ``_cfg``.
    """
    if str(getattr(cfg, "agent_spec", "") or "").strip():
        return False
    sources = getattr(cfg, "sources", {}) or {}
    return (
        sources.get("agent_module") == "default"
        and sources.get("agent_variable") == "default"
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
