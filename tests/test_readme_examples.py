"""Validate the runnable code examples in README.md so they cannot regress.

#72 — the README's "Using Custom Agents → Creating a Custom Agent" block (the
file it tells you to save as `my_agent.py`) shipped a placeholder line that is
not valid Python:

    tools=[...your_custom_tools...]

`[...your_custom_tools...]` is a `SyntaxError`, so a newcomer who copied the
block verbatim, pointed `LANGSTAGE_AGENT_SPEC=./my_agent.py:agent` at it, and
launched got an agent that never loaded (`could not load agent: invalid
syntax`) — on the primary "bring your own agent" onramp. The README frames the
block as a complete file, so the placeholder read as real code.

These tests lift the example straight out of README.md and (1) compile it, so a
`SyntaxError` fails the suite, and (2) load it through the *same* host loader the
launcher's `-a file.py:attr` uses, proving the documented file actually comes up
as a langstage-jupyter agent. Building the agent needs no provider API key (the
model is constructed lazily), so this runs in CI unmodified.
"""
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_README = (_REPO / "README.md").read_text(encoding="utf-8")


def _readme_code_block(heading: str, *, must_contain: str) -> str:
    """Return the first ```python fenced block after a README `### heading`.

    `must_contain` is a sanity anchor: if the README is restructured and this
    no longer captures the intended snippet, the test fails loudly here rather
    than silently validating the wrong block.
    """
    anchor = _README.index(f"### {heading}")
    m = re.search(r"```python\n(.*?)\n```", _README[anchor:], re.DOTALL)
    assert m, f"no ```python block found after '### {heading}'"
    code = m.group(1)
    assert must_contain in code, (
        f"README '{heading}' block no longer contains {must_contain!r} — "
        "the extraction anchor is stale, update this test"
    )
    return code


_CUSTOM_AGENT_EXAMPLE = _readme_code_block(
    "Creating a Custom Agent", must_contain="create_deep_agent"
)


def test_custom_agent_example_is_valid_python():
    """#72: the custom-agent block must compile — no `SyntaxError` verbatim."""
    # compile() raises SyntaxError (failing the test) on the bad placeholder line.
    compile(_CUSTOM_AGENT_EXAMPLE, "README.md#creating-a-custom-agent", "exec")


def test_custom_agent_example_loads_as_an_agent(tmp_path, monkeypatch):
    """#72: the documented file loads via the launcher's own `file.py:attr` loader.

    Writes the README block verbatim to `my_agent.py` and resolves it exactly as
    `langstage-jupyter -a ./my_agent.py:agent` does — through
    `langstage_core.load_agent_spec` — asserting it yields the named graph.
    """
    from langstage_core import load_agent_spec

    # No provider key needed to build; drop any so the test proves it too.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    agent_file = tmp_path / "my_agent.py"
    agent_file.write_text(_CUSTOM_AGENT_EXAMPLE, encoding="utf-8")

    graph = load_agent_spec(f"{agent_file}:agent")

    # The name the README sets, and a graph that could actually run a turn.
    assert getattr(graph, "name", None) == "my-custom-agent"
    assert hasattr(graph, "invoke"), "loaded object is not a runnable graph"
