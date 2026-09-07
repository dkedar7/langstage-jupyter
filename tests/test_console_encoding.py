"""Console-encoding resilience for the launcher's verdicts (gh #122/#126/#130/#140).

Every case below drives the real code path with ``sys.stdout``/``sys.stderr``
replaced by a cp1252 stream, which is what a default Windows console gives the
process. A bare ``print`` of agent-supplied text raises ``UnicodeEncodeError``
there, and the command dies with a traceback instead of its verdict.
"""
import io
import sys
from unittest.mock import patch

import pytest

from langstage_jupyter.launcher import _print, main


class _Cp1252:
    """A stdout that behaves like a default Windows console."""

    def __init__(self):
        self.buffer = io.BytesIO()
        self.stream = io.TextIOWrapper(self.buffer, encoding="cp1252", newline="")

    def __enter__(self):
        self._saved = (sys.stdout, sys.stderr)
        sys.stdout = sys.stderr = self.stream
        return self

    def __exit__(self, *exc):
        self.stream.flush()
        sys.stdout, sys.stderr = self._saved
        return False

    @property
    def text(self):
        self.stream.flush()
        return self.buffer.getvalue().decode("cp1252")


# The three shapes the issues name: an accented char, CJK, and an emoji.
UNENCODABLE = "clé introuvable 配置错误 ✅"


def test_print_escapes_what_the_console_cannot_encode():
    with _Cp1252() as console:
        _print(f"[fail] could not load agent: {UNENCODABLE}")
    said = console.text
    assert said.startswith("[fail] could not load agent: ")
    # Escaped, not dropped: the bytes are still recoverable from the output.
    assert "\\u914d\\u7f6e" in said or "\\xe9" in said


def test_print_leaves_encodable_text_exactly_alone():
    """The escape must not reach the common case: cp1252 has an accented e."""
    with _Cp1252() as console:
        _print("[ ok ] agent verified: café")
    assert console.text == "[ ok ] agent verified: café\n"


def test_print_writes_to_the_stream_it_was_given():
    with _Cp1252() as console:
        _print(f"[fail] {UNENCODABLE}", file=sys.stderr)
    assert console.text.startswith("[fail] ")


def _write_agent(tmp_path, body):
    path = tmp_path / "bad_agent.py"
    path.write_text(body, encoding="utf-8")
    return path


class TestVerdictsSurviveACp1252Console:
    """The commands themselves, not just the helper (gh #122/#130/#140).

    A test of ``_print`` alone would stay green if a call site went back to the
    bare ``print``, which is the whole defect. These drive ``main()``.
    """

    def test_verify_reports_a_load_failure_it_cannot_encode(self, monkeypatch, tmp_path):
        # The issue's own repro: a custom agent whose import raises with a non-Latin-1
        # message. --verify must print its [fail] verdict and exit 1, not die encoding it.
        agent = _write_agent(
            tmp_path, f'raise RuntimeError("{UNENCODABLE} (agent boot failed)")\n'
        )
        monkeypatch.setattr(
            "sys.argv", ["langstage-jupyter", "-a", f"{agent}:graph", "--verify"]
        )
        with _Cp1252() as console:
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 1
        assert "[fail] could not load agent:" in console.text

    def test_ask_prints_a_reply_it_cannot_encode(self, monkeypatch, tmp_path):
        # The keyless demo agent echoes the prompt, so a non-Latin-1 prompt comes back
        # as a non-Latin-1 reply: the shortest honest end-to-end for the reply path.
        monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", "")
        monkeypatch.setenv("DEEPAGENT_AGENT_SPEC", "")
        monkeypatch.setattr(
            "sys.argv", ["langstage-jupyter", "--demo", "--ask", UNENCODABLE]
        )
        with _Cp1252() as console:
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        assert "You said:" in console.text

    def test_show_config_prints_a_value_it_cannot_encode(self, monkeypatch, tmp_path):
        # gh #130: a CJK workspace or agent path is a config VALUE, and the human table
        # prints it raw where the --json twin escapes it through ensure_ascii.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", f"./{UNENCODABLE}.py:graph")
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--show-config"])
        with _Cp1252() as console:
            main()
        assert "agent_spec" in console.text
