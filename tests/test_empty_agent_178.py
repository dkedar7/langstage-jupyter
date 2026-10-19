"""gh #178: an empty ``-a`` value (``-a ""``, ``-a=``, ``--agent=``) is a usage error.

It used to skip the malformed-spec check (``""`` is falsy), so the launcher ran the
env / toml / default agent while ``--show-config`` advertised ``agent_spec = '' [override]``.
"""
import pytest

from langstage_jupyter.launcher import main


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    empty = tmp_path / "global"
    empty.mkdir()
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(empty))
    for var in ("LANGSTAGE_AGENT_SPEC", "DEEPAGENT_AGENT_SPEC"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "langstage_core.agui.verify",
        lambda *a, **k: pytest.fail("no agent may be verified for an empty -a"),
    )
    monkeypatch.setattr(
        "langstage_core.agui.collect_chunk_frames",
        lambda *a, **k: pytest.fail("no turn may run for an empty -a"),
    )
    return tmp_path


@pytest.mark.parametrize("argv", [
    ["-a", "", "--show-config"],
    ["-a=", "--show-config"],
    ["--agent=", "--show-config"],
    ["--agent", "", "--verify"],
    ["-a=", "--ask", "hi"],
])
def test_empty_agent_value_is_a_usage_error(isolated, monkeypatch, capsys, argv):
    monkeypatch.setattr("sys.argv", ["langstage-jupyter", *argv])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 64  # usage error (ADR 0007), like -a "  "
    captured = capsys.readouterr()
    assert "-a/--agent: Invalid agent spec ''" in captured.err
    assert "[override]" not in captured.out


def test_env_spec_is_not_masked_by_an_empty_flag(isolated, monkeypatch, capsys):
    monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", "langstage_core.demo.tools:graph")
    monkeypatch.setattr("sys.argv", ["langstage-jupyter", "-a", "", "--ask", "use a tool"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 64
    assert "Using agent" not in capsys.readouterr().err
