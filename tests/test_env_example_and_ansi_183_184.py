"""gh #183: copying ``.env.example`` doesn't turn on LangSmith tracing or fake an API key.
gh #184: ``execute_cell`` hands the agent plain text, not ANSI-colored tracebacks."""

from pathlib import Path

from dotenv import dotenv_values

from langstage_jupyter.notebook_tools import _output_text

REPO = Path(__file__).resolve().parents[1]


def test_copied_example_sets_no_langsmith_or_placeholder_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text((REPO / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    values = dotenv_values(env)
    assert not [k for k in values if k.startswith("LANGSMITH_")]
    assert "ANTHROPIC_API_KEY" not in values


def test_error_traceback_is_plain_text():
    out = {
        "output_type": "error", "ename": "KeyError", "evalue": "'missing'",
        "traceback": [
            "\x1b[31m-----\x1b[39m",
            "\x1b[31mKeyError\x1b[39m                    Traceback (most recent call last)",
            "\x1b[36mCell\x1b[39m\x1b[36m \x1b[39m\x1b[32mIn[1]\x1b[39m\x1b[32m, line 1\x1b[39m",
            "\x1b[32m----> \x1b[39m\x1b[32m1\x1b[39m {}[\x1b[33m\"missing\"\x1b[39m]",
            "\x1b[38;5;28;01mKeyError\x1b[39;00m: 'missing'",
        ],
    }
    text = _output_text(out)
    assert "\x1b" not in text
    assert text == ("ERROR: KeyError: 'missing'\n-----\n"
                    "KeyError                    Traceback (most recent call last)\n"
                    "Cell In[1], line 1\n----> 1 {}[\"missing\"]\nKeyError: 'missing'")


def test_stream_and_results_are_plain_text():
    assert _output_text({"output_type": "stream", "name": "stderr",
                         "text": "\x1b[1;33mWARNING\x1b[0m slow\n"}) == "[stderr] WARNING slow\n"
    assert _output_text({"output_type": "execute_result",
                         "data": {"text/plain": "\x1b[32m42\x1b[0m"}}) == "42"
    assert _output_text({"output_type": "display_data",
                         "data": {"text/plain": "\x1b[2K\rdone"}}) == "[display] \rdone"
