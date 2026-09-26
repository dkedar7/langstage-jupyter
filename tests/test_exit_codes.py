"""The family exit-code scheme (langstage-core ADR 0007).

0 success / 1 failure / 2 paused on a HITL interrupt / 64 usage error. Before this,
the launcher's usage errors (a flag without its value, --demo with -a, a malformed -a,
a bad --port) exited 1, and a `jupyter lab` child that exited 2 was propagated as 2,
which reads as "paused".
"""
from unittest.mock import MagicMock

import pytest

from langstage_jupyter import exit_codes
from langstage_jupyter.launcher import main


def _exit(monkeypatch, argv):
    monkeypatch.setattr("sys.argv", ["langstage-jupyter", *argv])
    with pytest.raises(SystemExit) as exc:
        main()
    return exc.value.code


def test_constants_are_the_family_scheme():
    assert (
        exit_codes.EXIT_OK, exit_codes.EXIT_FAIL, exit_codes.EXIT_PAUSED, exit_codes.EXIT_USAGE
    ) == (0, 1, 2, 64)


@pytest.mark.parametrize(
    "outcome, code", [("complete", 0), ("error", 1), ("interrupted", 2), ("weird", 1), (None, 1)]
)
def test_exit_code_for_outcome(outcome, code):
    assert exit_codes.exit_code_for_outcome(outcome) == code


@pytest.mark.parametrize(
    "argv",
    [
        ["-a"],                              # flag with no value (gh #115)
        ["--agent", "--demo"],               # value is another flag
        ["--ask"],                           # --ask with no prompt
        ["--ask", "--demo"],
        ["--demo", "-a", "x.py:graph"],      # conflicting flags
        ["--demo", "-a", "x.py:graph", "--verify"],
        ["-a", "my_agent.py"],               # malformed -a (no :attr)
        ["--port=notaport", "--no-browser"],
        ["--port=0"],
        ["--port", "70000"],
    ],
)
def test_usage_errors_exit_64(monkeypatch, capsys, argv):
    ran = []
    monkeypatch.setattr("langstage_jupyter.launcher.subprocess.run", lambda *a, **k: ran.append(a))
    assert _exit(monkeypatch, argv) == 64
    assert not ran


def test_verify_ok_0_and_ask_codes(monkeypatch, capsys):
    pytest.importorskip("ag_ui_langgraph")
    assert _exit(monkeypatch, ["--demo", "--verify"]) == 0
    assert _exit(monkeypatch, ["--demo", "--ask", "hi"]) == 0


def test_verify_load_failure_is_1(monkeypatch, capsys):
    assert _exit(monkeypatch, ["-a", "no_such_module_xyz:graph", "--verify"]) == 1


def test_ask_interrupt_is_2(monkeypatch, capsys):
    from langstage_jupyter import launcher

    monkeypatch.setattr(launcher, "ask", lambda prompt: exit_codes.exit_code_for_outcome("interrupted"))
    assert _exit(monkeypatch, ["--demo", "--ask", "hi"]) == 2


def test_check_connection_unconfigured_is_1(monkeypatch, capsys):
    monkeypatch.delenv("LANGSTAGE_JUPYTER_SERVER_URL", raising=False)
    monkeypatch.delenv("DEEPAGENT_JUPYTER_SERVER_URL", raising=False)
    assert _exit(monkeypatch, ["--check-connection"]) == 1


def test_no_free_port_is_a_clean_failure_1(monkeypatch, capsys):
    from langstage_jupyter import launcher

    def _none(*a, **k):
        raise RuntimeError("Could not find an available port")

    monkeypatch.setattr(launcher, "find_available_port", _none)
    ran = []
    monkeypatch.setattr("langstage_jupyter.launcher.subprocess.run", lambda *a, **k: ran.append(a))
    assert _exit(monkeypatch, ["--no-browser"]) == 1
    assert "Could not find an available port" in capsys.readouterr().err
    assert not ran


@pytest.mark.parametrize("child, expected", [(0, 0), (1, 1), (2, 1), (3, 3)])
def test_jupyter_child_exit_propagates_except_2(monkeypatch, capsys, child, expected):
    # A busy --port makes jupyter lab exit 1; its argparse errors exit 2. The launcher
    # propagates the child's code (gh #62) but never as 2, which the family reserves for
    # "paused on a HITL interrupt".
    monkeypatch.setattr(
        "langstage_jupyter.launcher.subprocess.run", lambda *a, **k: MagicMock(returncode=child)
    )
    assert _exit(monkeypatch, ["--port=19199", "--no-browser"]) == expected


def test_help_lists_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--help"])
    main()
    out = capsys.readouterr().out
    assert "Exit codes" in out and "64" in out
