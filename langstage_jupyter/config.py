"""Configuration management for langstage-jupyter.

Shares the ``DEEPAGENT_*`` schema + TOML loader with the deep-agent family via
``langstage_core.host``. ``LabConfig`` is the full resolved config
(``defaults < deepagents.toml < DEEPAGENT_* env < overrides``) used by the
launcher and `--show-config`. The module-level constants below are an
env+defaults view (no TOML) kept for back-compat with existing call sites
(``agent.py``, ``agent_wrapper.py``).
"""
import math
import os
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar, Optional

from langstage_core.host import HostConfig, load_toml_config  # noqa: F401  (re-exported for callers)
from langstage_core.host import parse_agent_spec
from langstage_core.host.config import _env_bool_strict, _value_issue, _warn_invalid_value


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


def _positive_seconds(value: Any) -> float:
    """``execute_timeout`` must be > 0. A 0 or negative value used to be applied as-is,
    so every ``execute_cell`` hit its deadline before reading a message and reported a
    spurious timeout (gh #142). ``nan`` fails the same test."""
    seconds = float(value)
    if not seconds > 0:
        raise ValueError(f"must be a positive number of seconds, got {seconds!r}")
    return seconds


def _sampling_temperature(value: Any) -> float:
    """``model_temperature`` must be a finite number >= 0 for every provider. A negative,
    ``nan`` or ``inf`` value used to be applied as-is, so the default agent's first turn
    failed with a provider 400 that never named the setting (gh #144). The upper bound is
    provider-specific and checked in :meth:`LabConfig.resolve`."""
    temperature = float(value)
    if not math.isfinite(temperature) or temperature < 0:
        raise ValueError(f"must be a finite number >= 0, got {temperature!r}")
    return temperature


#: The highest sampling temperature each provider's API accepts. Anthropic's Messages
#: API takes 0-1; OpenAI (incl. Azure) and Gemini take 0-2. A provider missing here gets
#: no upper bound (Ollama and friends accept larger values). (gh #144)
_PROVIDER_MAX_TEMPERATURE = {
    "anthropic": 1.0,
    "openai": 2.0,
    "azure_openai": 2.0,
    "google_genai": 2.0,
    "google_vertexai": 2.0,
}


# Bare-name prefixes -> provider, used only if langchain's own parser can't be imported.
# Mirrors the common cases of langchain's ``_attempt_infer_model_provider`` (gh #136).
_FALLBACK_PROVIDER_PREFIXES = (
    (("gpt-", "o1", "o3", "chatgpt", "text-davinci"), "openai"),
    (("claude",), "anthropic"),
    (("command",), "cohere"),
    (("mistral", "mixtral"), "mistralai"),
    (("deepseek",), "deepseek"),
    (("grok",), "xai"),
)


def infer_model_provider(model_name: str) -> str:
    """The provider ``init_chat_model(model_name)`` would pick, or ``""`` if none.

    The default agent builds its model with ``init_chat_model(MODEL_NAME)``, which accepts
    both ``provider:model`` and a bare model name whose provider it infers
    (``claude-sonnet-4-5`` -> anthropic, ``gpt-4o`` -> openai). Reading only the
    ``provider:`` prefix made the key preflight skip every bare name, so ``/health`` showed a
    false green and ``--verify`` hit a raw provider error (gh #136). So this asks langchain's
    own parser, which is the one ``init_chat_model`` calls, and falls back to the common
    prefixes only if that private helper ever moves. Lives here (not in ``handlers``) so
    config validation can use it without importing the server (gh #144).
    """
    try:
        from langchain.chat_models.base import _parse_model
    except ImportError:  # pragma: no cover - langchain moved its private helper
        _parse_model = None
    if _parse_model is not None:
        try:
            with warnings.catch_warnings():
                # A bare 'gemini-*' name warns about a future provider default change.
                warnings.simplefilter("ignore")
                return _parse_model(model_name, None)[1]
        except Exception:  # noqa: BLE001 - "can't infer" is ValueError; anything else too
            return ""
    lowered = model_name.lower()
    if ":" in lowered:
        return lowered.split(":", 1)[0]
    for prefixes, provider in _FALLBACK_PROVIDER_PREFIXES:
        if lowered.startswith(prefixes):
            return provider
    return ""


def _strip_trailing_slash(value: Any) -> str:
    """Normalize ``jupyter_server_url`` once for every consumer. A trailing slash (as
    copied from the browser) made the notebook tools request ``//api/contents/...``,
    which 404s, while ``--check-connection`` stripped it and passed (gh #154)."""
    return str(value).rstrip("/")


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

    # Semantic checks core's resolve() runs after every layer; a value that raises here
    # degrades to the field default with a `note:` (the core mechanism, gh langstage #123).
    _VALIDATORS: ClassVar[dict] = {
        "execute_timeout": _positive_seconds,
        "jupyter_server_url": _strip_trailing_slash,
        "model_temperature": _sampling_temperature,
    }

    @classmethod
    def resolve(cls, **kwargs) -> "LabConfig":
        """Core's layered resolve, plus the provider-specific temperature ceiling.

        A field validator only sees its own value, and the ceiling depends on the model's
        provider (``1.5`` is fine for OpenAI, a 400 for Anthropic). An out-of-range value
        degrades to the default with the same ``note:`` and ``config_issues()`` entry as a
        field validator, so ``--show-config`` never shows it as live (gh #144).
        """
        cfg = super().resolve(**kwargs)
        cfg._cap_temperature_for_provider()
        return cfg

    def _cap_temperature_for_provider(self) -> None:
        temperature = self.model_temperature
        # Skip the provider lookup (which imports langchain) for the usual in-range value.
        if temperature is None or temperature <= min(_PROVIDER_MAX_TEMPERATURE.values()):
            return
        provider = infer_model_provider(str(self.model_name or "").strip())
        ceiling = _PROVIDER_MAX_TEMPERATURE.get(provider)
        if ceiling is None or temperature <= ceiling:
            return
        default = type(self).__dataclass_fields__["model_temperature"].default
        exc = ValueError(
            f"{provider} models accept 0-{ceiling:g} (model_name={self.model_name!r}), "
            f"got {temperature!r}"
        )
        _warn_invalid_value("model_temperature", temperature, exc, default)
        issues = getattr(self, "_value_issues", None)
        if isinstance(issues, list):
            issues.append(_value_issue(
                "invalid_value", "model_temperature",
                self.sources.get("model_temperature", "default"), temperature, exc, default,
            ))
        self.model_temperature = default
        self.sources["model_temperature"] = "default"

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


#: The module the bundled default agent lives in, and the variable it exports.
BUNDLED_AGENT_MODULE = "langstage_jupyter.agent"
BUNDLED_AGENT_VARIABLE = "agent"


def runs_bundled_default(cfg: "LabConfig") -> bool:
    """True when the agent that will run IS the bundled default, however it was selected.

    :func:`is_bundled_default` answers "was no agent configured?". This also covers the
    default picked by name: ``LANGSTAGE_AGENT_SPEC=langstage_jupyter.agent:agent`` (the
    example ``.env.example`` gives) or ``LANGSTAGE_AGENT_MODULE=langstage_jupyter.agent``.
    Those run the same agent with the same model, so they need the same key. Keying the
    missing-key preflight off ``is_bundled_default`` sent them to a raw provider
    ``TypeError`` on the first turn instead (gh #112). A malformed spec is never the
    default (it fails to load, gh #151).
    """
    if is_bundled_default(cfg):
        return True
    spec = str(getattr(cfg, "agent_spec", "") or "").strip()
    if spec:
        try:
            module, variable = parse_agent_spec(spec)
        except ValueError:
            return False
    else:
        module = getattr(cfg, "agent_module", None)
        variable = getattr(cfg, "agent_variable", None)
    return module == BUNDLED_AGENT_MODULE and variable in (None, "", BUNDLED_AGENT_VARIABLE)


def workspace_serving_mismatch(serving_root: Any, pinned_root: Any) -> Optional[str]:
    """The warning for a pinned workspace that is not the JupyterLab serving root, or None.

    The notebook tools go through the Jupyter contents API, so they (like kernels and
    open tabs) always work in the serving root; the agent's file tools use the pinned
    workspace. When the two differ they land in different directories (gh #150). The
    launcher avoids that by serving the pinned workspace; this names the split when it
    can't (an explicit ``--notebook-dir`` / ``--ServerApp.root_dir``, or plain
    ``jupyter lab``). ADR 0006 keeps JupyterLab's root authoritative for notebooks.
    """
    if not serving_root or not pinned_root:
        return None
    serving = Path(str(serving_root)).expanduser().resolve()
    pinned = Path(str(pinned_root)).expanduser().resolve()
    if serving == pinned:
        return None
    return (
        f"Warning: the pinned workspace {pinned} (LANGSTAGE_WORKSPACE_ROOT / workspace.root) "
        f"is not the JupyterLab serving root {serving}. The agent's file tools use {pinned}, "
        f"but its notebook tools (create_notebook, execute_cell, ...) use {serving}, where "
        "JupyterLab keeps notebooks and kernels. To make them agree, launch with "
        "`langstage-jupyter` without --notebook-dir (it serves the pinned workspace), or "
        f"start Jupyter with --ServerApp.root_dir={pinned}."
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
