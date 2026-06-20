"""Regressions for the canonical-env / TOML config bugs (dogfood clusters 2+3).

- Canonical ``LANGSTAGE_*`` env vars must take effect (not only legacy
  ``DEEPAGENT_*``), including ``execute_timeout`` (was read via a DEEPAGENT-only
  helper) and ``workspace_root`` (was read with inverted precedence).
- The module-level constants in ``config.py`` (which the default agent reads)
  must honor ``langstage.toml`` — the same resolution ``--show-config`` shows.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from langstage_jupyter.config import LabConfig, get_config


# ── resolve(): canonical LANGSTAGE_* env ─────────────────────────────


def test_canonical_env_sets_execute_timeout():
    cfg = LabConfig.resolve(env={"LANGSTAGE_EXECUTE_TIMEOUT": "42"}, use_toml=False)
    assert cfg.execute_timeout == 42.0


def test_canonical_env_beats_legacy():
    cfg = LabConfig.resolve(
        env={
            "LANGSTAGE_MODEL_NAME": "openai:canonical",
            "DEEPAGENT_MODEL_NAME": "openai:legacy",
        },
        use_toml=False,
    )
    assert cfg.model_name == "openai:canonical"


def test_legacy_env_still_resolves():
    cfg = LabConfig.resolve(env={"DEEPAGENT_EXECUTE_TIMEOUT": "99"}, use_toml=False)
    assert cfg.execute_timeout == 99.0


# ── get_config(): canonical first, legacy fallback ───────────────────


def test_get_config_prefers_canonical(monkeypatch):
    monkeypatch.setenv("LANGSTAGE_EXECUTE_TIMEOUT", "5")
    monkeypatch.setenv("DEEPAGENT_EXECUTE_TIMEOUT", "9")
    assert get_config("execute_timeout", type_cast=float) == 5.0


def test_get_config_legacy_fallback(monkeypatch):
    monkeypatch.delenv("LANGSTAGE_EXECUTE_TIMEOUT", raising=False)
    monkeypatch.setenv("DEEPAGENT_EXECUTE_TIMEOUT", "9")
    assert get_config("execute_timeout", type_cast=float) == 9.0


# ── module constants (import-time) honor canonical env + TOML ────────
#
# config.py's constants are computed at import, so these run a subprocess with
# the env / cwd set up first — exactly the dogfood repro shape.


def _agent_const(name: str, *, env: dict | None = None, cwd: Path | None = None) -> str:
    full_env = dict(os.environ)
    # Strip any inherited config env so the subprocess starts clean.
    for k in list(full_env):
        if k.startswith(("LANGSTAGE_", "DEEPAGENT_")):
            del full_env[k]
    full_env.update(env or {})
    out = subprocess.run(
        [sys.executable, "-c", f"import langstage_jupyter.agent as a; print(a.{name})"],
        capture_output=True,
        text=True,
        env=full_env,
        cwd=str(cwd) if cwd else None,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_module_execute_timeout_honors_canonical_env():
    assert _agent_const("EXECUTE_TIMEOUT", env={"LANGSTAGE_EXECUTE_TIMEOUT": "42"}) == "42.0"


def test_module_constants_honor_langstage_toml(tmp_path):
    (tmp_path / "langstage.toml").write_text(
        '[model]\nname = "anthropic:toml-model"\n[jupyter]\nexecute_timeout = 123.0\n',
        encoding="utf-8",
    )
    assert _agent_const("MODEL_NAME", cwd=tmp_path) == "anthropic:toml-model"
    assert _agent_const("EXECUTE_TIMEOUT", cwd=tmp_path) == "123.0"
