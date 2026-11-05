"""gh #193: stopping the launcher must stop the JupyterLab server it started.

The launcher used ``subprocess.run`` and handled only Ctrl+C, so SIGTERM/SIGHUP (``kill
<pid>``, ``timeout``, ``docker stop``) killed the launcher and orphaned the server on its
port with its kernels. These tests run ``_run_child`` in a real helper process with a
stand-in child and stop the helper the way those tools do.
"""
import os
import signal
import subprocess
import sys
import textwrap
import time
from unittest.mock import MagicMock

import pytest

from langstage_jupyter import launcher

# A stand-in for `python -m jupyterlab`: writes its pid, then serves forever.
_CHILD = "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(600)"


def _alive(pid):
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie (exited, not yet reaped by init) counts as gone.
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def _wait_until(pred, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.1)
    return pred()


@pytest.fixture
def launched(tmp_path):
    """Start a helper that runs the stand-in child through ``_run_child``."""
    pidfile = tmp_path / "child.pid"
    helper = textwrap.dedent(
        f"""
        import sys
        from langstage_jupyter.launcher import _run_child
        r = _run_child([sys.executable, "-c", {_CHILD!r}, {str(pidfile)!r}])
        sys.exit(r.returncode)
        """
    )
    procs = []

    def start():
        p = subprocess.Popen([sys.executable, "-c", helper])
        procs.append(p)
        assert _wait_until(lambda: pidfile.exists() and pidfile.read_text()), "child never started"
        return p, int(pidfile.read_text())

    yield start
    for p in procs:  # never leak a helper (or, via the fix, its child) from a failed test
        if p.poll() is None:
            p.kill()
            p.wait()
    if pidfile.exists() and pidfile.read_text():
        pid = int(pidfile.read_text())
        if _alive(pid):
            try:
                os.kill(pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
            except OSError:
                pass


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
@pytest.mark.parametrize("signame", ["SIGTERM", "SIGHUP"])
def test_termination_signal_is_forwarded_to_the_server(launched, signame):
    helper, child = launched()
    helper.send_signal(getattr(signal, signame))
    rc = helper.wait(timeout=30)
    assert _wait_until(lambda: not _alive(child)), f"server orphaned after {signame}"
    # The stand-in dies of the forwarded signal; the launcher reports it like a shell.
    assert rc == 128 + getattr(signal, signame)


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_sigint_to_the_launcher_alone_stops_the_server(launched):
    # `kill -INT <launcher>` never reaches the server by itself.
    helper, child = launched()
    helper.send_signal(signal.SIGINT)
    assert helper.wait(timeout=30) == 0
    assert _wait_until(lambda: not _alive(child)), "server orphaned after SIGINT"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="PR_SET_PDEATHSIG is Linux-only")
def test_server_dies_with_a_sigkilled_launcher_on_linux(launched):
    helper, child = launched()
    helper.kill()
    helper.wait(timeout=30)
    assert _wait_until(lambda: not _alive(child)), "server orphaned after SIGKILL"


@pytest.mark.skipif(os.name != "nt", reason="Windows job object")
def test_server_dies_with_a_hard_killed_launcher_on_windows(launched):
    # taskkill /F (TerminateProcess) runs no handler; the job object takes the server down.
    helper, child = launched()
    subprocess.run(["taskkill", "/F", "/PID", str(helper.pid)], capture_output=True)
    helper.wait(timeout=30)
    assert _wait_until(lambda: not _alive(child)), "server orphaned after taskkill /F"


def test_normal_exit_code_is_returned_and_handlers_restored():
    before = signal.getsignal(signal.SIGTERM)
    r = launcher._run_child([sys.executable, "-c", "raise SystemExit(3)"])
    assert r.returncode == 3
    assert signal.getsignal(signal.SIGTERM) is before


def test_ctrl_c_during_wait_stops_the_server_and_exits_0(monkeypatch):
    proc = MagicMock()
    proc.wait.side_effect = [KeyboardInterrupt, 0]
    proc.poll.return_value = 0
    proc.returncode = 0
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(launcher, "_bind_to_windows_job", lambda p: None)
    assert launcher._run_child(["jupyter"]).returncode == 0
    proc.terminate.assert_called_once()


def test_server_that_ignores_the_signal_is_killed_after_the_grace_period(monkeypatch):
    proc = MagicMock()
    proc.wait.side_effect = [KeyboardInterrupt, subprocess.TimeoutExpired("jupyter", 1), 0]
    proc.poll.return_value = 0
    proc.returncode = 0
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(launcher, "_bind_to_windows_job", lambda p: None)
    launcher._run_child(["jupyter"])
    proc.kill.assert_called_once()


def test_ctrl_c_message_uses_the_current_name(monkeypatch, capsys):
    proc = MagicMock()
    proc.wait.side_effect = [KeyboardInterrupt, 0]
    proc.poll.return_value = 0
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(launcher, "_bind_to_windows_job", lambda p: None)
    launcher._run_child(["jupyter"])
    out = capsys.readouterr().out
    assert "langstage-jupyter" in out and "DeepAgent" not in out
