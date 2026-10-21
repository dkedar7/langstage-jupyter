"""gh #181: the launcher refuses the ``.env.example`` placeholder as an auth token.

``.env.example`` shipped ``LANGSTAGE_JUPYTER_TOKEN=change-me`` uncommented, and the launcher
loads the project ``.env``, so copying the example (as it instructs) pinned a publicly known
token on a server that runs code. The example now leaves it commented out, and a placeholder
token from any source is a usage error (exit 64) before JupyterLab starts.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from langstage_jupyter.launcher import main

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def launch(tmp_path, monkeypatch):
    empty = tmp_path / "global"
    empty.mkdir()
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(empty))
    for var in ("LANGSTAGE_JUPYTER_TOKEN", "DEEPAGENT_JUPYTER_TOKEN", "JUPYTER_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    calls = {}

    def fake_run(cmd, env=None):
        calls["cmd"] = cmd

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr("langstage_jupyter.launcher.subprocess.run", fake_run)

    def run(*extra):
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--no-browser", "--port", "8991", *extra])
        with pytest.raises(SystemExit) as exc:
            main()
        return exc.value.code, calls

    return run


@pytest.mark.parametrize("var", ["LANGSTAGE_JUPYTER_TOKEN", "JUPYTER_TOKEN"])
@pytest.mark.parametrize("value", ["change-me", "CHANGE-ME", " changeme "])
def test_placeholder_from_env_is_refused(launch, monkeypatch, capsys, var, value):
    monkeypatch.setenv(var, value)
    code, calls = launch()
    assert code == 64
    assert "cmd" not in calls  # JupyterLab never started
    err = capsys.readouterr().err
    assert "placeholder" in err and "generates a fresh token" in err


def test_placeholder_from_dotenv_is_refused(tmp_path):
    """The issue's exact path, in a fresh process: importing the package loads the project's
    .env (as a real launch does), and the copied placeholder must not reach JupyterLab."""
    (tmp_path / ".env").write_text("LANGSTAGE_JUPYTER_TOKEN=change-me\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in ("LANGSTAGE_JUPYTER_TOKEN", "DEEPAGENT_JUPYTER_TOKEN", "JUPYTER_TOKEN")}
    env["LANGSTAGE_CONFIG_HOME"] = str(tmp_path / "global")
    # JupyterLab itself is stubbed out (exit 99), so a regression can't start a real server.
    code = ("import sys, langstage_jupyter.launcher as L; "
            "L.subprocess.run = lambda *a, **k: sys.exit(99); "
            "sys.argv = ['langstage-jupyter', '--no-browser', '--port', '8992']; L.main()")
    r = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 64, r.stdout + r.stderr
    assert "placeholder" in r.stderr and "Launching:" not in r.stdout


def test_placeholder_token_arg_is_refused(launch, capsys):
    code, calls = launch("--IdentityProvider.token=change-me")
    assert code == 64 and "cmd" not in calls


def test_a_real_pinned_token_still_works(launch, monkeypatch):
    monkeypatch.setenv("LANGSTAGE_JUPYTER_TOKEN", "a-real-secret-token-123456")
    code, calls = launch()
    assert code == 0
    assert "--IdentityProvider.token=a-real-secret-token-123456" in calls["cmd"]


def test_auth_disabled_on_purpose_is_still_honoured(launch):
    code, calls = launch("--IdentityProvider.token=")
    assert code == 0
    assert "--IdentityProvider.token=" in calls["cmd"]


def test_copying_the_example_leaves_the_token_generated(launch, tmp_path, monkeypatch):
    from dotenv import dotenv_values

    (tmp_path / ".env").write_text((REPO / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    for k, v in dotenv_values(tmp_path / ".env").items():  # what the package import loads
        if v is not None:
            monkeypatch.setenv(k, v)
    code, calls = launch()
    assert code == 0
    tok = next(a for a in calls["cmd"] if a.startswith("--IdentityProvider.token="))
    assert len(tok.split("=", 1)[1]) >= 32  # a fresh token, not the placeholder


def test_example_has_no_active_jupyter_server_settings():
    active = [ln for ln in (REPO / ".env.example").read_text(encoding="utf-8").splitlines()
              if ln.startswith(("LANGSTAGE_JUPYTER_TOKEN", "LANGSTAGE_JUPYTER_SERVER_URL"))]
    assert active == []
