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


# ── gh #75: a malformed numeric env var must degrade gracefully, not crash ──
#
# Sibling of the already-fixed #42 (malformed langstage.toml). Before the fix,
# a non-numeric LANGSTAGE_MODEL_TEMPERATURE / LANGSTAGE_EXECUTE_TIMEOUT let a raw
# ValueError from float() escape resolve() and take down every entrypoint —
# including a bare `import langstage_jupyter`, since config resolves at import
# time. It must now fall back to the field default with a one-line stderr note
# that names the offending variable + value (parity with #42's TOML path).

# (canonical env var, field, expected default) for the two lenient-cast knobs.
_NUMERIC_KNOBS = [
    ("LANGSTAGE_MODEL_TEMPERATURE", "model_temperature", 0.0),
    ("LANGSTAGE_EXECUTE_TIMEOUT", "execute_timeout", 300.0),
]


@pytest.mark.parametrize("var, field, default", _NUMERIC_KNOBS)
@pytest.mark.parametrize("bad", ["abc", "0,5", "5m", "0.0.0", " "])
def test_malformed_numeric_env_falls_back_to_default(
    isolated, tmp_path, capsys, var, field, default, bad
):
    """A malformed numeric env value resolves to the default (no exception)."""
    cfg = LabConfig.resolve(env={var: bad}, toml_start=tmp_path)  # must NOT raise
    assert getattr(cfg, field) == default


@pytest.mark.parametrize("var, field, default", _NUMERIC_KNOBS)
def test_malformed_numeric_env_notes_the_variable_and_value(
    isolated, tmp_path, capsys, var, field, default
):
    """The stderr note names the offending variable and the bad value it choked on."""
    LabConfig.resolve(env={var: "abc"}, toml_start=tmp_path)
    err = capsys.readouterr().err
    assert "malformed" in err
    assert var in err          # names the canonical LANGSTAGE_* variable
    assert "abc" in err        # ...and the value that failed to parse
    assert str(default) in err  # ...and the default it fell back to


@pytest.mark.parametrize("var, field, default", _NUMERIC_KNOBS)
def test_malformed_numeric_env_via_legacy_spelling(
    isolated, tmp_path, capsys, var, field, default
):
    """The legacy DEEPAGENT_* spelling degrades identically (same caster)."""
    legacy = var.replace("LANGSTAGE_", "DEEPAGENT_")
    cfg = LabConfig.resolve(env={legacy: "abc"}, toml_start=tmp_path)  # must NOT raise
    assert getattr(cfg, field) == default
    assert "malformed" in capsys.readouterr().err


@pytest.mark.parametrize("var, field, value", [
    ("LANGSTAGE_MODEL_TEMPERATURE", "model_temperature", "0.7"),
    ("LANGSTAGE_EXECUTE_TIMEOUT", "execute_timeout", "60"),
])
def test_valid_numeric_env_still_parses(isolated, tmp_path, capsys, var, field, value):
    """A well-formed value still parses to a float, with no note printed."""
    cfg = LabConfig.resolve(env={var: value}, toml_start=tmp_path)
    assert getattr(cfg, field) == float(value)
    assert "malformed" not in capsys.readouterr().err


@pytest.mark.parametrize("var, field, default", _NUMERIC_KNOBS)
def test_unset_numeric_env_uses_default_without_note(
    isolated, tmp_path, capsys, var, field, default
):
    """Unset (and empty, treated as unset) keeps the default and prints no note."""
    for env in ({}, {var: ""}):
        cfg = LabConfig.resolve(env=env, toml_start=tmp_path)
        assert getattr(cfg, field) == default
    assert "malformed" not in capsys.readouterr().err


# ── gh #78: a wrong-TYPE value in langstage.toml ─────────────────────────────
# The untreated sibling of #75 (which hardened the *env* casters only). A quoted
# number in TOML is syntactically valid but the wrong type, and it used to be
# handed through verbatim: `execute_timeout = "300"` became the str '300' for a
# field declared float, --show-config stripped the quotes so it looked correct,
# and the defect surfaced far away as
# `TypeError: unsupported operand type(s) for +: 'float' and 'str'` the first
# time notebook_tools.py ran a cell. The repair lives in langstage-core's
# HostConfig._coerce (>= 1.0.21), which LabConfig resolves through; these pin
# that it actually reaches this stage.

@pytest.mark.parametrize("body, field, expected", [
    ('[jupyter]\nexecute_timeout = "300"\n', "execute_timeout", 300.0),
    ('[model]\ntemperature = "0.5"\n', "model_temperature", 0.5),
])
def test_quoted_number_in_toml_is_coerced(isolated, tmp_path, body, field, expected):
    _toml(tmp_path, body)
    cfg = LabConfig.resolve(env={}, toml_start=tmp_path)
    value = getattr(cfg, field)
    assert value == expected
    assert isinstance(value, float), f"{field} resolved as {type(value).__name__}"


def test_coerced_timeout_survives_the_cell_execution_arithmetic(isolated, tmp_path):
    """The exact expression from the issue: notebook_tools.py's deadline math."""
    import time

    _toml(tmp_path, '[jupyter]\nexecute_timeout = "300"\n')
    cfg = LabConfig.resolve(env={}, toml_start=tmp_path)
    assert time.monotonic() + cfg.execute_timeout > 0  # used to raise TypeError


@pytest.mark.parametrize("body, field, default", [
    ('[jupyter]\nexecute_timeout = "not-a-number"\n', "execute_timeout", 300.0),
    ("[model]\ntemperature = true\n", "model_temperature", 0.0),
])
def test_uncoercible_toml_value_degrades_with_a_note(
    isolated, tmp_path, capsys, body, field, default
):
    """Uncoercible keeps the default AND the 'default' source attribution, so
    --show-config can never present an unusable value as a live TOML setting."""
    _toml(tmp_path, body)
    cfg = LabConfig.resolve(env={}, toml_start=tmp_path)
    assert getattr(cfg, field) == default
    assert cfg.sources[field] == "default"
    assert "malformed" in capsys.readouterr().err


# ── gh #83: a malformed env var must not clobber a valid langstage.toml value ────
@pytest.mark.parametrize("var, field, toml_body, toml_val", [
    ("LANGSTAGE_MODEL_TEMPERATURE", "model_temperature", "[model]\ntemperature = 0.7\n", 0.7),
    ("LANGSTAGE_EXECUTE_TIMEOUT", "execute_timeout", "[jupyter]\nexecute_timeout = 120\n", 120.0),
])
def test_malformed_env_keeps_the_toml_value(isolated, tmp_path, capsys, var, field, toml_body, toml_val):
    """The precedence half of #83: env sits above TOML, so a REJECTED env var must
    fall through to the TOML value that a valid config set — not skip straight to the
    built-in default (which the old _lenient_number wrapper did), and --show-config
    must credit toml, not the rejected env var."""
    _toml(tmp_path, toml_body)
    cfg = LabConfig.resolve(env={var: "notanumber"}, toml_start=tmp_path)

    assert getattr(cfg, field) == toml_val, "malformed env clobbered the langstage.toml value"
    assert cfg.sources[field].startswith("toml"), "source mislabeled as env for a rejected value"
    # the note names what it kept — the TOML value, not "default"
    err = capsys.readouterr().err
    assert "ignoring malformed" in err and str(toml_val) in err


# ── gh #86: --show-config footer must not report a malformed langstage.toml as absent ──
# The stderr note (from langstage-core) already says "ignoring malformed config <path>",
# i.e. the file WAS found. But describe()'s footer keyed purely off the successfully-parsed
# paths, so a present-but-unparseable file fell into the same "no langstage.toml ... found"
# branch as a genuinely absent one — two contradictory statements in one command, steering
# a user debugging "why isn't my langstage.toml applied?" at file location instead of the
# real syntax typo. The three config states must render three distinct footers.

# The issue's exact repro: a [jupyter table header missing its closing ']'.
_MALFORMED_TOML = '[model]\nname = "my-model"\ntemperature = 0.5\n[jupyter\nvirtual_mode = false\n'


def test_show_config_footer_flags_malformed_toml_as_found(isolated, tmp_path, capsys):
    """Present-but-malformed: the footer must NOT say 'no ... found', and must name the
    file as found-but-malformed — agreeing with the stderr 'ignoring malformed' note."""
    (tmp_path / "langstage.toml").write_text(_MALFORMED_TOML)
    text = LabConfig.resolve(env={}, toml_start=tmp_path).describe()

    assert "no langstage.toml" not in text, "malformed file still reported as absent (gh #86)"
    assert "malformed" in text, "footer should say the found file is malformed"
    assert str(tmp_path / "langstage.toml") in text, "footer should name the found file"
    # ...and it agrees with the stderr note langstage-core emits for the same file.
    assert "ignoring malformed config" in capsys.readouterr().err


def test_show_config_footer_valid_toml_still_reads(isolated, tmp_path):
    """A VALID file still shows the normal 'TOML read from: <path>' footer (no regression)."""
    (tmp_path / "langstage.toml").write_text('[model]\nname = "my-model"\n')
    text = LabConfig.resolve(env={}, toml_start=tmp_path).describe()

    assert "TOML read from:" in text
    assert str(tmp_path / "langstage.toml") in text
    assert "malformed" not in text
    assert "no langstage.toml" not in text


def test_show_config_footer_absent_toml_still_reports_not_found(isolated, tmp_path):
    """Genuine absence still shows the 'no langstage.toml ... found' footer (no regression)."""
    text = LabConfig.resolve(env={}, toml_start=tmp_path).describe()

    assert "TOML: no langstage.toml (or legacy deepagents.toml) found" in text
    assert "malformed" not in text
