"""Notebook tools: data-integrity, error contract, kernel readiness.

These are the first tests of the notebook toolset — it previously lived inside
agent.py, which builds a chat model at import, so it couldn't be reached without
an API key. It now lives in langstage_jupyter.notebook_tools.

Findings covered (all reproduced by dogfooding 0.6.13 against a real Jupyter):
  * create_notebook silently destroyed an existing notebook
  * reads used the process cwd while writes used the server root (split-brain)
  * execute_cell raised IndexError; missing notebooks raised FileNotFoundError
  * modify_cell deleted the cell on an empty string
  * execute_cell never waited for the kernel to be ready (dropped its output)
"""
import queue

import nbformat
import pytest
import requests

from langstage_jupyter import notebook_tools as nt


@pytest.fixture
def offline(monkeypatch):
    """No Jupyter server reachable, so _load_notebook and _save_notebook fall back
    to the filesystem TOGETHER — they can never disagree about which file they mean."""

    def boom(*a, **k):
        raise requests.RequestException("no server")

    monkeypatch.setattr(nt.requests, "get", boom)
    monkeypatch.setattr(nt.requests, "put", boom)
    monkeypatch.setattr(nt.requests, "post", boom)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _cells(path):
    return nbformat.read(path, as_version=4).cells


# ── create_notebook must not destroy work ────────────────────────────


def test_create_notebook_refuses_to_overwrite_an_existing_notebook(offline, ws):
    nt.create_notebook("keep.ipynb")
    nt.insert_code_cell("result = 42", "keep.ipynb")
    nt.insert_code_cell("print(result)", "keep.ipynb")
    assert len(_cells("keep.ipynb")) == 2

    out = nt.create_notebook("keep.ipynb")  # the destructive call

    assert "already exists" in out
    assert len(_cells("keep.ipynb")) == 2, "must NOT wipe the user's cells"


def test_create_notebook_overwrite_is_opt_in(offline, ws):
    nt.create_notebook("z.ipynb")
    nt.insert_code_cell("x = 1", "z.ipynb")
    out = nt.create_notebook("z.ipynb", overwrite=True)
    assert "Created" in out
    assert len(_cells("z.ipynb")) == 0


# ── one authority: reads come from the server, like writes ───────────


def test_load_and_save_both_go_through_the_contents_api(monkeypatch, ws):
    """The notebook exists ONLY on the server (nothing on disk) — a read must still
    find it. Previously reads used nbformat.read() against the process cwd, so a
    notebook in the server's root_dir was invisible and edits split-brained."""
    server_nb = nbformat.v4.new_notebook()
    server_nb.cells.append(nbformat.v4.new_code_cell(source="on_the_server = True"))
    seen = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"content": server_nb}

    def fake_get(url, **kw):
        seen["get"] = url
        seen["get_params"] = kw.get("params")
        return Resp()

    def fake_put(url, **kw):
        seen["put"] = url
        seen["body"] = kw["json"]
        return Resp()

    monkeypatch.setattr(nt.requests, "get", fake_get)
    monkeypatch.setattr(nt.requests, "put", fake_put)

    out = nt.insert_code_cell("added = 1", "remote.ipynb")

    assert "Inserted code cell at index 1" in out
    assert seen["get"].endswith("/api/contents/remote.ipynb")
    assert seen["put"].endswith("/api/contents/remote.ipynb")
    # The contents API's `format` is text/base64 (for files). Sending format=json for a
    # notebook makes it reject the GET, and we'd silently fall back to the filesystem —
    # re-opening the very split-brain this fixes. Only `type` belongs here.
    assert "format" not in (seen["get_params"] or {})
    assert [c.source for c in seen["body"]["content"]["cells"]] == ["on_the_server = True", "added = 1"]
    assert not (ws / "remote.ipynb").exists(), "must not have written a stray cwd copy"


# ── error contract: actionable strings, never raw exceptions ─────────


@pytest.mark.parametrize(
    "call",
    [
        lambda: nt.insert_code_cell("x = 1", "missing.ipynb"),
        lambda: nt.modify_cell("missing.ipynb", 0, "x = 1"),
        lambda: nt.delete_cell("missing.ipynb", 0),
        lambda: nt.execute_cell("missing.ipynb", 0),
        lambda: nt.read_cell("missing.ipynb", 0),
        lambda: nt.get_notebook_state("missing.ipynb"),
    ],
)
def test_missing_notebook_returns_an_error_string_not_an_exception(offline, ws, call):
    out = call()  # used to raise FileNotFoundError from 3 of these
    assert isinstance(out, str) and out.startswith("Error:")
    assert "not found" in out.lower()


@pytest.mark.parametrize(
    "call",
    [
        lambda: nt.execute_cell("n.ipynb", 99),
        lambda: nt.modify_cell("n.ipynb", 99, "x = 1"),
        lambda: nt.delete_cell("n.ipynb", 99),
        lambda: nt.read_cell("n.ipynb", 99),
    ],
)
def test_out_of_range_index_returns_an_error_string_not_indexerror(offline, ws, call):
    nt.create_notebook("n.ipynb")
    nt.insert_code_cell("x = 1", "n.ipynb")
    out = call()  # execute_cell used to raise a raw IndexError here
    assert isinstance(out, str) and "out of range" in out


# ── modify vs delete ─────────────────────────────────────────────────


def test_modify_cell_no_longer_deletes_on_an_empty_string(offline, ws):
    nt.create_notebook("m.ipynb")
    nt.insert_code_cell("keep_me = 1", "m.ipynb")
    out = nt.modify_cell("m.ipynb", 0, "")
    assert out.startswith("Error:") and "delete_cell" in out
    assert len(_cells("m.ipynb")) == 1, "an empty string must not silently delete"


def test_delete_cell_removes_the_cell(offline, ws):
    nt.create_notebook("d.ipynb")
    nt.insert_code_cell("a = 1", "d.ipynb")
    nt.insert_code_cell("b = 2", "d.ipynb")
    assert "Deleted cell at index 0" in nt.delete_cell("d.ipynb", 0)
    assert [c.source for c in _cells("d.ipynb")] == ["b = 2"]


def test_modify_cell_clears_stale_outputs(offline, ws):
    nt.create_notebook("o.ipynb")
    nt.insert_code_cell("x = 1", "o.ipynb")
    nb = nbformat.read("o.ipynb", as_version=4)
    nb.cells[0].execution_count = 7
    nb.cells[0].outputs = [nbformat.v4.new_output("stream", name="stdout", text="stale")]
    nbformat.write(nb, "o.ipynb")

    nt.modify_cell("o.ipynb", 0, "x = 2")

    c = _cells("o.ipynb")[0]
    assert c.source == "x = 2" and c.outputs == [] and c.execution_count is None


# ── markdown authoring (gh #70) ──────────────────────────────────────


def test_insert_markdown_cell_appends_a_markdown_cell(offline, ws):
    nt.create_notebook("md.ipynb")
    out = nt.insert_markdown_cell("# My Analysis\nIntro prose.", "md.ipynb")
    assert "Inserted markdown cell at index 0" in out
    cell = _cells("md.ipynb")[0]
    assert cell.cell_type == "markdown"
    assert cell.source == "# My Analysis\nIntro prose."


def test_insert_markdown_cell_at_index_interleaves_with_code(offline, ws):
    # The core Jupyter workflow: a markdown header above a code cell.
    nt.create_notebook("mix.ipynb")
    nt.insert_code_cell("import pandas as pd", "mix.ipynb")
    nt.insert_markdown_cell("# Title", "mix.ipynb", 0)  # insert BEFORE the code
    cells = _cells("mix.ipynb")
    assert [c.cell_type for c in cells] == ["markdown", "code"]
    assert cells[0].source == "# Title"


def test_insert_markdown_cell_missing_notebook_is_an_error_string(offline, ws):
    out = nt.insert_markdown_cell("# hi", "nope.ipynb")
    assert isinstance(out, str) and out.startswith("Error:") and "not found" in out.lower()


def test_insert_markdown_cell_out_of_range_index(offline, ws):
    nt.create_notebook("r.ipynb")
    out = nt.insert_markdown_cell("# hi", "r.ipynb", 5)
    assert "out of range" in out


def test_modify_cell_can_edit_a_markdown_cell(offline, ws):
    # gh #70: modify_cell used to hard-refuse any non-code cell, so a title/header
    # a user already wrote could not be edited. It must now replace markdown source.
    nt.create_notebook("edit.ipynb")
    nt.insert_markdown_cell("# Old Title", "edit.ipynb")
    out = nt.modify_cell("edit.ipynb", 0, "# Revised Title")
    assert "Modified cell at index 0" in out
    cell = _cells("edit.ipynb")[0]
    assert cell.cell_type == "markdown"  # type preserved
    assert cell.source == "# Revised Title"


def test_modify_cell_empty_string_still_refuses_for_markdown(offline, ws):
    # The empty-string sentinel must carry over to markdown — an empty edit must
    # not silently blank/delete the cell (gh #70).
    nt.create_notebook("s.ipynb")
    nt.insert_markdown_cell("# Keep", "s.ipynb")
    out = nt.modify_cell("s.ipynb", 0, "")
    assert out.startswith("Error:") and "delete_cell" in out
    assert _cells("s.ipynb")[0].source == "# Keep"


def test_markdown_tools_are_registered_and_state_counts_them(offline, ws):
    # The tool must actually be handed to the agent, and get_notebook_state (which
    # already previews markdown) must reflect an inserted markdown cell.
    assert nt.insert_markdown_cell in nt.NOTEBOOK_TOOLS
    nt.create_notebook("c.ipynb")
    nt.insert_markdown_cell("# Heading", "c.ipynb")
    state = nt.get_notebook_state("c.ipynb")
    assert "Markdown cells: 1" in state
    assert "(markdown) # Heading" in state


def test_read_cell_returns_full_source(offline, ws):
    nt.create_notebook("r.ipynb")
    nt.insert_code_cell("line1\nline2", "r.ipynb")
    out = nt.read_cell("r.ipynb", 0)
    assert "line1\nline2" in out


def test_get_notebook_state_previews_cells(offline, ws):
    nt.create_notebook("s.ipynb")
    nt.insert_code_cell("import pandas as pd", "s.ipynb")
    out = nt.get_notebook_state("s.ipynb")
    assert "Total cells: 1" in out
    assert "import pandas as pd" in out, "state should show what's in the notebook"


# ── kernel readiness + stale-client eviction ─────────────────────────


class _FakeClient:
    """Mimics the readiness handshake: kernel_info on shell, then an iopub drain."""

    def __init__(self, alive=True):
        self.alive = alive
        self.waited = False           # kernel_info round-trip completed
        self.iopub_drained = False
        self.channels_started = False
        self.stopped = False

    def load_connection_file(self, cf):
        pass

    def start_channels(self):
        self.channels_started = True

    def kernel_info(self):
        self.waited = True

    def get_shell_msg(self, timeout=None):
        return {"header": {"msg_type": "kernel_info_reply"}}

    def get_iopub_msg(self, timeout=None):
        self.iopub_drained = True
        raise queue.Empty  # nothing queued -> drain completes

    def is_alive(self):
        return self.alive

    def stop_channels(self):
        self.stopped = True


@pytest.fixture
def fake_kernel(monkeypatch):
    made = []

    def factory():
        c = _FakeClient()
        made.append(c)
        return c

    monkeypatch.setattr(nt, "BlockingKernelClient", factory)
    monkeypatch.setattr(nt, "start_notebook_kernel", lambda p: "kid")
    monkeypatch.setattr(nt, "find_connection_file", lambda k: "conn.json")
    nt.kernel_clients.clear()
    return made


def test_kernel_client_waits_for_ready_before_use(fake_kernel):
    """Without wait_for_ready() a cold kernel's iopub messages are dropped (ZMQ
    slow-joiner): the cell ran, but the tool saw no output and reported a false
    EXECUTE_TIMEOUT with execution_count=None."""
    client = nt._connect_kernel("nb.ipynb")
    assert client.channels_started and client.waited and client.iopub_drained


def test_a_dead_cached_kernel_client_is_replaced(fake_kernel):
    first = nt._connect_kernel("nb.ipynb")
    assert nt._connect_kernel("nb.ipynb") is first, "a live client is reused"

    first.alive = False  # e.g. the user restarted the kernel from the UI
    second = nt._connect_kernel("nb.ipynb")

    assert second is not first, "a dead client must be evicted, not reused forever"
    assert first.stopped and second.waited
