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
import os
import queue
import time

import nbformat
import pytest
import requests

from langstage_jupyter import notebook_tools as nt


@pytest.fixture
def offline(monkeypatch):
    """No Jupyter server reachable, so _load_notebook and _save_notebook fall back
    to the filesystem TOGETHER — they can never disagree about which file they mean."""

    def boom(*a, **k):
        raise requests.ConnectionError("no server")

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


# ── kernel lookup: EXACT path, never substring (gh #96) ──────────────


def _fake_sessions(monkeypatch, sessions):
    class Resp:
        status_code = 200

        def json(self):
            return sessions

    monkeypatch.setattr(nt.requests, "get", lambda *a, **k: Resp())


def test_kernel_lookup_matches_exact_path_not_substring(monkeypatch):
    # gh #96: "a.ipynb" is a substring of "data.ipynb" ("data.ipynb".endswith("a.ipynb")).
    # A substring match resolved a.ipynb to data.ipynb's kernel, so execute_cell("a.ipynb")
    # ran in data.ipynb's namespace (state bleed) and saved outputs back into a.ipynb.
    # Each notebook must resolve to its OWN session by exact path.
    _fake_sessions(monkeypatch, [
        {"notebook": {"path": "data.ipynb"}, "kernel": {"id": "KID-DATA"}},
        {"notebook": {"path": "a.ipynb"}, "kernel": {"id": "KID-A"}},
    ])
    assert nt.get_notebook_kernel_id("a.ipynb") == "KID-A"        # not KID-DATA
    assert nt.get_notebook_kernel_id("data.ipynb") == "KID-DATA"


def test_short_name_does_not_borrow_a_longer_notebooks_kernel(monkeypatch):
    # Only data.ipynb has a live kernel; a.ipynb (its suffix) has none. The lookup must
    # raise ValueError (no session) so a FRESH kernel is started for a.ipynb — never
    # silently reuse data.ipynb's, which is the cross-notebook bleed (gh #96).
    _fake_sessions(monkeypatch, [
        {"notebook": {"path": "data.ipynb"}, "kernel": {"id": "KID-DATA"}},
    ])
    assert nt.get_notebook_kernel_id("data.ipynb") == "KID-DATA"
    with pytest.raises(ValueError):
        nt.get_notebook_kernel_id("a.ipynb")


def test_kernel_lookup_normalizes_leading_slashes(monkeypatch):
    # A leading-slash request normalizes to the same path as the session, so it still
    # matches exactly (the normalization is what makes exact-match robust).
    _fake_sessions(monkeypatch, [
        {"notebook": {"path": "nb.ipynb"}, "kernel": {"id": "KID"}},
    ])
    assert nt.get_notebook_kernel_id("/nb.ipynb") == "KID"


# ── create_notebook in a missing subdirectory (gh #97) ───────────────


def test_create_notebook_in_missing_subdir_offline(offline, ws):
    # gh #97: create_notebook("reports/sub/x.ipynb") when reports/ doesn't exist used to
    # raise an uncaught FileNotFoundError (the contents-API PUT 500s, then the filesystem
    # fallback nbformat.write raises). It must instead mkdir -p the parent and create the
    # notebook — there's no separate folder tool, so this is the agent's only way to nest.
    out = nt.create_notebook("reports/sub/summary.ipynb")
    assert isinstance(out, str) and out.startswith("Created")
    assert (ws / "reports" / "sub" / "summary.ipynb").exists()
    assert _cells("reports/sub/summary.ipynb") == []  # a valid, empty notebook


def test_create_notebook_bare_filename_still_works(offline, ws):
    # The parent-dir logic must be a no-op for a top-level notebook (no crash, no stray dir).
    out = nt.create_notebook("top.ipynb")
    assert out.startswith("Created")
    assert (ws / "top.ipynb").exists()


def test_create_notebook_creates_parent_dir_via_server(monkeypatch, ws):
    # With a live server, the parent directory is created through the contents API (PUT
    # type=directory) before the notebook PUT — so the server's root_dir stays the
    # authority and the notebook write no longer 500s on a missing parent (gh #97).
    puts = []

    class Resp:
        status_code = 201

        def json(self):
            return {}

    def fake_get(url, **kw):
        r = Resp()
        r.status_code = 404  # _notebook_exists: not there yet
        return r

    def fake_put(url, **kw):
        puts.append((url, kw.get("json", {}).get("type")))
        return Resp()

    monkeypatch.setattr(nt.requests, "get", fake_get)
    monkeypatch.setattr(nt.requests, "put", fake_put)

    out = nt.create_notebook("analysis/report.ipynb")
    assert out.startswith("Created")
    # The parent dir was created (type=directory) BEFORE the notebook (type=notebook).
    types_by_path = {url.rsplit("/api/contents/", 1)[-1]: typ for url, typ in puts}
    assert types_by_path.get("analysis") == "directory"
    assert types_by_path.get("analysis/report.ipynb") == "notebook"


# ── gh #117: a path outside the serving root is rejected, never written ──────
# `create_notebook("../x.ipynb")` used to report success while writing OUTSIDE the
# serving root: requests collapsed `/api/contents/../x.ipynb` to a 404, which the
# disk fallback then honored with `nbformat.write("../x.ipynb")` relative to cwd.


@pytest.fixture
def no_http(monkeypatch):
    """Record any server call a tool makes for a path it should have refused."""
    calls = []

    def record(*a, **k):
        calls.append(a)
        raise requests.ConnectionError("no server")

    for verb in ("get", "put", "post"):
        monkeypatch.setattr(nt.requests, verb, record)
    return calls


_ESCAPING_PATHS = [
    "../escape.ipynb",
    "sub/../../escape.ipynb",
    "a/b/../../../escape.ipynb",
    "..\\escape.ipynb",
    "C:/escape.ipynb",
    "C:\\escape.ipynb",
    "\\\\host\\share\\escape.ipynb",
]

_ALL_TOOLS = [
    lambda p: nt.create_notebook(p),
    lambda p: nt.create_notebook(p, overwrite=True),
    lambda p: nt.insert_code_cell("x = 1", p),
    lambda p: nt.insert_markdown_cell("# t", p),
    lambda p: nt.modify_cell(p, 0, "x = 2"),
    lambda p: nt.delete_cell(p, 0),
    lambda p: nt.execute_cell(p, 0),
    lambda p: nt.read_cell(p, 0),
    lambda p: nt.get_notebook_state(p),
]


@pytest.mark.parametrize("path", _ESCAPING_PATHS)
@pytest.mark.parametrize("call", _ALL_TOOLS)
def test_a_path_outside_the_serving_root_is_rejected(no_http, ws, call, path):
    out = call(path)
    assert isinstance(out, str) and out.startswith("Error:"), out
    assert "outside" in out
    assert not no_http, "a refused path must not reach the server"
    assert not (ws.parent / "escape.ipynb").exists(), "wrote outside the serving root"


def test_create_notebook_dotdot_does_not_escape_offline(offline, ws):
    # The issue's exact repro: no server reachable, so the disk fallback honored `..`.
    root = ws / "root"
    root.mkdir()
    os.chdir(root)  # the ws fixture's monkeypatch.chdir restores cwd afterwards
    out = nt.create_notebook("../escape.ipynb")
    assert out.startswith("Error:")
    assert not (ws / "escape.ipynb").exists()


def test_a_leading_slash_still_means_the_serving_root(offline, ws):
    # The contents API treats "/x.ipynb" as root-relative; that stays accepted.
    assert nt.create_notebook("/top.ipynb").startswith("Created")
    assert (ws / "top.ipynb").exists()


def test_a_dotdot_that_stays_inside_is_still_refused_plainly(offline, ws):
    # "a/../b.ipynb" never leaves the root, but a `..` segment is refused outright:
    # simpler to reason about than normalizing, and no agent needs it.
    out = nt.create_notebook("a/../b.ipynb")
    assert out.startswith("Error:") and ".." in out


# ── gh #125: an auth failure is an error, not a silent switch to local disk ───
# The primitives fell back to the local filesystem on ANY status other than 200/404,
# so a wrong/stale token (403) quietly read and wrote the agent's cwd instead of the
# server root: an existing notebook reported "not found" and new ones landed in the
# wrong place, both as success. Disk fallback is only for an unreachable server.


def _status_server(monkeypatch, status):
    class Resp:
        status_code = status
        reason = "Forbidden" if status == 403 else "Error"
        text = "denied"

        def json(self):
            return {}

    def respond(url, **kw):
        return Resp()

    for verb in ("get", "put", "post"):
        monkeypatch.setattr(nt.requests, verb, respond)


@pytest.mark.parametrize("status", [401, 403, 500])
@pytest.mark.parametrize("call", _ALL_TOOLS)
def test_a_server_error_status_surfaces_instead_of_falling_back_to_disk(
    monkeypatch, ws, call, status
):
    # A same-named notebook sits in the agent's cwd; a fallback would read/write it.
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("x = 1"))
    nbformat.write(nb, str(ws / "report.ipynb"))
    before = (ws / "report.ipynb").read_bytes()
    _status_server(monkeypatch, status)

    out = call("report.ipynb")

    assert out.startswith("Error:"), out
    assert f"HTTP {status}" in out
    assert "not found" not in out.lower(), "a server error was reported as a missing notebook"
    assert (ws / "report.ipynb").read_bytes() == before, "fell back to the local copy"


def test_a_403_names_the_token(monkeypatch, ws):
    _status_server(monkeypatch, 403)
    out = nt.get_notebook_state("report.ipynb")
    assert "LANGSTAGE_JUPYTER_TOKEN" in out


def test_create_in_subdir_on_403_creates_nothing_on_disk(monkeypatch, ws):
    _status_server(monkeypatch, 403)
    out = nt.create_notebook("reports/new.ipynb")
    assert out.startswith("Error:")
    assert not (ws / "reports").exists()


def test_a_non_connection_request_error_does_not_fall_back(monkeypatch, ws):
    # A read timeout means the server IS there (it accepted the connection); writing to
    # local disk instead would be the same silent split. Only "unreachable" falls back.
    def slow(*a, **k):
        raise requests.ReadTimeout("slow")

    for verb in ("get", "put", "post"):
        monkeypatch.setattr(nt.requests, verb, slow)
    out = nt.create_notebook("new.ipynb")
    assert out.startswith("Error:")
    assert not (ws / "new.ipynb").exists()


def test_an_unreachable_server_still_falls_back_to_disk(ws, monkeypatch):
    def refused(*a, **k):
        raise requests.ConnectionError("refused")

    for verb in ("get", "put", "post"):
        monkeypatch.setattr(nt.requests, verb, refused)
    assert nt.create_notebook("offline.ipynb").startswith("Created")
    assert (ws / "offline.ipynb").exists()


# ── gh #116 / #128: execute_cell timeouts interrupt, never wipe; no stdin ─────


def _msg(msg_type, content, parent="MID"):
    return {"header": {"msg_type": msg_type}, "parent_header": {"msg_id": parent},
            "content": content}


class _ExecClient:
    """A kernel client that plays a script of iopub messages.

    ``before`` is emitted right away; ``after`` only once the kernel has been
    interrupted (as a real kernel emits KeyboardInterrupt + idle after SIGINT)."""

    def __init__(self, before, after=(), state=None):
        self.before, self.after = list(before), list(after)
        self.state = state if state is not None else {"interrupted": False}
        self.execute_kwargs = None

    def execute(self, source, **kwargs):
        self.execute_kwargs = kwargs
        return "MID"

    def get_iopub_msg(self, timeout=None):
        if self.before:
            return self.before.pop(0)
        if self.state["interrupted"] and self.after:
            return self.after.pop(0)
        time.sleep(0.01)
        raise queue.Empty


@pytest.fixture
def exec_nb(offline, ws, monkeypatch):
    """A notebook on disk whose cell 0 carries outputs from an earlier run, plus a
    fake interrupt endpoint that records its calls."""
    nb = nbformat.v4.new_notebook()
    cell = nbformat.v4.new_code_cell("slow()")
    cell.execution_count = 7
    cell.outputs = [nbformat.v4.new_output("stream", name="stdout", text="earlier run\n")]
    nb.cells.append(cell)
    nbformat.write(nb, str(ws / "nb.ipynb"))

    state = {"interrupted": False, "posts": []}

    def fake_post(url, **kw):
        state["posts"].append(url)
        state["interrupted"] = True

        class R:
            status_code = 204
        return R()

    monkeypatch.setattr(nt.requests, "post", fake_post)
    monkeypatch.setattr(nt, "get_notebook_kernel_id", lambda p: "KID")
    monkeypatch.setattr(nt, "EXECUTE_TIMEOUT", 0.3)
    monkeypatch.setattr(nt, "_INTERRUPT_GRACE", 1.0)
    return state


def _use_client(monkeypatch, client):
    monkeypatch.setattr(nt, "_connect_kernel", lambda p: client)


def test_execute_cell_does_not_offer_stdin(exec_nb, monkeypatch):
    # gh #128: with allow_stdin=True and nothing servicing stdin, `input()` parks the
    # kernel for the whole EXECUTE_TIMEOUT and then wedges it. False makes the kernel
    # raise StdinNotImplementedError at once, like `nbconvert --execute`.
    client = _ExecClient([_msg("execute_input", {"execution_count": 8}),
                          _msg("status", {"execution_state": "idle"})])
    _use_client(monkeypatch, client)
    nt.execute_cell("nb.ipynb", 0)
    assert client.execute_kwargs.get("allow_stdin") is False


def test_a_timed_out_cell_interrupts_the_kernel_and_keeps_its_partial_output(
    exec_nb, monkeypatch
):
    # gh #116: the timeout used to abandon the cell with the kernel still busy, so every
    # later execute queued behind it and "timed out" too.
    client = _ExecClient(
        before=[_msg("execute_input", {"execution_count": 8}),
                _msg("stream", {"name": "stdout", "text": "partial\n"})],
        after=[_msg("error", {"ename": "KeyboardInterrupt", "evalue": "",
                              "traceback": ["KeyboardInterrupt"]}),
               _msg("status", {"execution_state": "idle"})],
        state=exec_nb,
    )
    _use_client(monkeypatch, client)

    out = nt.execute_cell("nb.ipynb", 0)

    assert exec_nb["posts"] and exec_nb["posts"][0].endswith("/api/kernels/KID/interrupt")
    assert "EXECUTE_TIMEOUT" in out and "interrupted" in out
    cell = _cells("nb.ipynb")[0]
    assert cell.execution_count == 8
    assert "partial\n" in [o.get("text") for o in cell.outputs], "partial output lost"
    assert any(o.output_type == "error" and o.ename == "KeyboardInterrupt" for o in cell.outputs)


def test_a_cell_that_never_started_keeps_its_existing_outputs(exec_nb, monkeypatch):
    # The kernel is busy with something else (e.g. a long cell the user ran), so our
    # request never started before the budget ran out. The cell's earlier outputs must
    # survive (they used to be overwritten with [] and execution_count None), and the
    # kernel must NOT be interrupted: the running execution isn't ours to kill.
    client = _ExecClient(before=[], state=exec_nb)
    _use_client(monkeypatch, client)

    out = nt.execute_cell("nb.ipynb", 0)

    assert not exec_nb["posts"], "interrupted an execution that is not this cell's"
    assert "did not start" in out
    cell = _cells("nb.ipynb")[0]
    assert cell.execution_count == 7
    assert [o.text for o in cell.outputs] == ["earlier run\n"], "existing outputs were wiped"


def test_a_failed_interrupt_is_reported(exec_nb, monkeypatch):
    client = _ExecClient(before=[_msg("execute_input", {"execution_count": 8})])
    _use_client(monkeypatch, client)

    def refused(url, **kw):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(nt.requests, "post", refused)
    out = nt.execute_cell("nb.ipynb", 0)
    assert "could not interrupt" in out


@pytest.mark.parametrize("arg", [0, -1])
def test_execute_cell_names_the_cell_index_not_the_kernel_counter(exec_nb, monkeypatch, arg):
    # gh #118: the header echoed the kernel's In[N] counter in the slot every other
    # tool uses for the positional index, so an index-tracking agent saw a different cell.
    client = _ExecClient([_msg("execute_input", {"execution_count": 8}),
                          _msg("status", {"execution_state": "idle"})])
    _use_client(monkeypatch, client)
    out = nt.execute_cell("nb.ipynb", arg)
    assert out.startswith("Executed cell [0] (In[8]) in ")


# ── gh #123: clear_output / update_display_data are honored like JupyterLab ────


def test_clear_output_wait_drops_what_came_before(exec_nb, monkeypatch):
    client = _ExecClient([
        _msg("execute_input", {"execution_count": 8}),
        _msg("stream", {"name": "stdout", "text": "STALE_before\n"}),
        _msg("clear_output", {"wait": True}),
        _msg("stream", {"name": "stdout", "text": "CORRECT_after\n"}),
        _msg("status", {"execution_state": "idle"}),
    ])
    _use_client(monkeypatch, client)

    out = nt.execute_cell("nb.ipynb", 0)

    assert "STALE_before" not in out and "CORRECT_after" in out
    assert [o.get("text") for o in _cells("nb.ipynb")[0].outputs] == ["CORRECT_after\n"]


def test_clear_output_wait_with_nothing_after_keeps_the_old_output(exec_nb, monkeypatch):
    # wait=True defers the clear until new output arrives; none does, so nothing clears.
    client = _ExecClient([
        _msg("execute_input", {"execution_count": 8}),
        _msg("stream", {"name": "stdout", "text": "kept\n"}),
        _msg("clear_output", {"wait": True}),
        _msg("status", {"execution_state": "idle"}),
    ])
    _use_client(monkeypatch, client)
    nt.execute_cell("nb.ipynb", 0)
    assert [o.get("text") for o in _cells("nb.ipynb")[0].outputs] == ["kept\n"]


def test_clear_output_without_wait_clears_at_once(exec_nb, monkeypatch):
    client = _ExecClient([
        _msg("execute_input", {"execution_count": 8}),
        _msg("stream", {"name": "stdout", "text": "gone\n"}),
        _msg("clear_output", {"wait": False}),
        _msg("status", {"execution_state": "idle"}),
    ])
    _use_client(monkeypatch, client)
    out = nt.execute_cell("nb.ipynb", 0)
    assert _cells("nb.ipynb")[0].outputs == []
    assert "gone" not in out


def test_update_display_data_replaces_the_displayed_value(exec_nb, monkeypatch):
    client = _ExecClient([
        _msg("execute_input", {"execution_count": 8}),
        _msg("display_data", {"data": {"text/plain": "'V1'"}, "metadata": {},
                              "transient": {"display_id": "d1"}}),
        _msg("update_display_data", {"data": {"text/plain": "'V2_FINAL'"}, "metadata": {},
                                     "transient": {"display_id": "d1"}}),
        _msg("status", {"execution_state": "idle"}),
    ])
    _use_client(monkeypatch, client)

    out = nt.execute_cell("nb.ipynb", 0)

    outputs = _cells("nb.ipynb")[0].outputs
    assert [o.data["text/plain"] for o in outputs] == ["'V2_FINAL'"]
    assert "V2_FINAL" in out and "'V1'" not in out
    assert "transient" not in outputs[0], "transient display ids are not saved to the file"


# ── gh #124: an unreachable server is an Error string from execute_cell too ─────


def test_execute_cell_with_the_server_down_returns_an_error_string(offline, ws):
    nt.kernel_clients.clear()
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("1 + 1"))
    nbformat.write(nb, str(ws / "d.ipynb"))

    out = nt.execute_cell("d.ipynb", 0)

    assert out.startswith("Error:"), out
    assert "kernel" in out


# ── gh #127: the path is percent-encoded into the contents URL ──────────────────


@pytest.mark.parametrize("path, encoded", [
    ("Experiment #3.ipynb", "Experiment%20%233.ipynb"),
    ("query?a.ipynb", "query%3Fa.ipynb"),
    ("sub dir/50%.ipynb", "sub%20dir/50%25.ipynb"),
])
def test_contents_url_percent_encodes_the_path(monkeypatch, path, encoded):
    monkeypatch.setattr(nt, "JUPYTER_SERVER_URL", "http://localhost:8888")
    assert nt._contents_url(path) == f"http://localhost:8888/api/contents/{encoded}"


def test_create_notebook_with_a_hash_reaches_the_full_path(monkeypatch, ws):
    seen = []

    class R:
        status_code = 404

    class Created:
        status_code = 201

    monkeypatch.setattr(nt.requests, "get", lambda url, **kw: seen.append(url) or R())
    monkeypatch.setattr(nt.requests, "put", lambda url, **kw: seen.append(url) or Created())
    assert nt.create_notebook("Experiment #3.ipynb").startswith("Created")
    assert seen and all(u.endswith("/api/contents/Experiment%20%233.ipynb") for u in seen)
