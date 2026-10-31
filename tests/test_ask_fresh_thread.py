"""`--ask` and `--serve-check` run each invocation on a fresh thread (langstage-core #221).

`--ask` used the constant thread "ask". With an agent that brings its own durable
checkpointer, every run then saw the earlier runs' history, and one paused run left every
later `--ask` replaying that interrupt (exit 2). The agent here keeps one module-level
saver across calls, standing in for a SQLite/Postgres store.
"""
import sys
import types

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import interrupt

from langstage_jupyter.launcher import ask

MOD = "_lj_durable_agent"


def _node(state):
    if state["messages"][-1].content == "ask me":
        interrupt({"question": "name?"})
    humans = [m.content for m in state["messages"] if m.type == "human"]
    return {"messages": [AIMessage(content=f"seen {len(humans)}: {humans}")]}


@pytest.fixture
def saver(tmp_path, monkeypatch):
    empty = tmp_path / "global"
    empty.mkdir()
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(empty))
    for var in ("LANGSTAGE_AGENT_MODULE", "DEEPAGENT_AGENT_MODULE", "DEEPAGENT_AGENT_SPEC"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    g = StateGraph(MessagesState)
    g.add_node("n", _node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    s = InMemorySaver()
    mod = types.ModuleType(MOD)
    mod.graph = g.compile(checkpointer=s)
    monkeypatch.setitem(sys.modules, MOD, mod)
    monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", f"{MOD}:graph")
    return s


def _threads(saver):
    return {c.config["configurable"]["thread_id"] for c in saver.list(None)}


def test_ask_runs_do_not_share_history(saver, capsys):
    assert ask("run 1") == 0
    assert ask("run 2") == 0
    out = capsys.readouterr().out
    assert "seen 1: ['run 1']" in out
    assert "seen 1: ['run 2']" in out  # was "seen 2: ['run 1', 'run 2']"


def test_a_paused_ask_does_not_stick_later_runs(saver, capsys):
    assert ask("ask me") == 2
    assert ask("hello") == 0  # was 2: the old interrupt replayed
    assert "seen 1: ['hello']" in capsys.readouterr().out


def test_each_ask_uses_its_own_thread(saver, capsys):
    for m in ("a", "b", "c"):
        ask(m)
    threads = _threads(saver)
    assert len(threads) == 3
    assert all(t.startswith("ask-") for t in threads)


def test_an_explicit_thread_is_kept(saver, capsys):
    ask("one", thread_id="mine")
    ask("two", thread_id="mine")
    assert _threads(saver) == {"mine"}
    assert "seen 2: ['one', 'two']" in capsys.readouterr().out


def test_verify_uses_a_fresh_thread_per_probe(saver, monkeypatch, capsys):
    from langstage_jupyter.launcher import main

    for _ in range(2):
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--verify"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
    threads = _threads(saver)
    assert len(threads) == 2 and all(t.startswith("verify-") for t in threads)
