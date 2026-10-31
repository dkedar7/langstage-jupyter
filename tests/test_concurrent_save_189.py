"""gh #189: execute_cell saved the notebook it read before the run, so anything saved
while the cell ran (a cell the user added, an autosave, another tool) was overwritten.

The fake kernel below rewrites the notebook on disk in the middle of the run, the way
a JupyterLab tab's save does, and the tests check what execute_cell leaves behind."""

import queue

import nbformat
import pytest
import requests

from langstage_jupyter import notebook_tools as nt


@pytest.fixture
def ws(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("no server")

    # No server: load and save fall back to the file, so the test edits the same copy.
    for verb in ("get", "put", "post"):
        monkeypatch.setattr(nt.requests, verb, boom)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _msg(msg_type, content):
    return {"header": {"msg_type": msg_type}, "parent_header": {"msg_id": "MID"},
            "content": content}


class _Client:
    """Plays one execution; ``during`` runs once the cell has started, before it ends."""

    def __init__(self, during):
        self.msgs = [_msg("execute_input", {"execution_count": 3}),
                     _msg("stream", {"name": "stdout", "text": "ran\n"}),
                     "DURING",
                     _msg("status", {"execution_state": "idle"})]
        self.during = during

    def execute(self, source, **kwargs):
        return "MID"

    def get_iopub_msg(self, timeout=None):
        if not self.msgs:
            raise queue.Empty
        m = self.msgs.pop(0)
        if m == "DURING":
            self.during()
            return self.get_iopub_msg(timeout)
        return m


def _write(cells, minor=5):
    nb = nbformat.v4.new_notebook()
    nb.nbformat_minor = minor
    nb.cells = cells
    if minor < 5:
        for c in nb.cells:
            c.pop("id", None)
    nbformat.write(nb, "nb.ipynb") if minor >= 5 else open("nb.ipynb", "w").write(
        nbformat.writes(nb, version=4))


def _read():
    return nbformat.read("nb.ipynb", as_version=4).cells


def _edit(fn):
    def during():
        nb = nbformat.read("nb.ipynb", as_version=4)
        fn(nb.cells)
        nbformat.write(nb, "nb.ipynb")
    return during


def _code(src, cid=None):
    c = nbformat.v4.new_code_cell(src)
    if cid:
        c.id = cid
    return c


def _md(src, cid=None):
    c = nbformat.v4.new_markdown_cell(src)
    if cid:
        c.id = cid
    return c


def _run(monkeypatch, during, index=0):
    monkeypatch.setattr(nt, "_connect_kernel", lambda p: _Client(during))
    return nt.execute_cell("nb.ipynb", index)


def test_a_cell_added_during_the_run_survives(ws, monkeypatch):
    _write([_code("slow()", "slow")])
    out = _run(monkeypatch, _edit(lambda cells: cells.append(_md("USER NOTE", "note"))))
    cells = _read()
    assert [c.source for c in cells] == ["slow()", "USER NOTE"], "the user's cell was lost"
    assert [o.get("text") for o in cells[0].outputs] == ["ran\n"]
    assert cells[0].execution_count == 3
    assert out.startswith("Executed cell [0] (In[3]) in nb.ipynb:")


def test_an_edit_to_another_cell_during_the_run_survives(ws, monkeypatch):
    _write([_code("slow()", "slow"), _code("x = 1", "other")])

    def edit(cells):
        cells[1].source = "x = 2  # saved by the user"
    _run(monkeypatch, _edit(edit))
    assert _read()[1].source == "x = 2  # saved by the user"


def test_outputs_follow_the_cell_when_cells_are_inserted_above_it(ws, monkeypatch):
    _write([_code("slow()", "slow")])
    out = _run(monkeypatch, _edit(lambda cells: cells.insert(0, _md("Title", "t"))))
    cells = _read()
    assert [c.source for c in cells] == ["Title", "slow()"]
    assert [o.get("text") for o in cells[1].outputs] == ["ran\n"]
    assert out.startswith("Executed cell [1] (In[3])"), "the index must be where the cell is now"
    assert "moved from [0] to [1]" in out


def test_a_cell_deleted_during_the_run_is_not_resurrected(ws, monkeypatch):
    _write([_code("slow()", "slow"), _md("keep", "keep")])
    out = _run(monkeypatch, _edit(lambda cells: cells.pop(0)))
    assert [c.source for c in _read()] == ["keep"], "the deleted cell came back"
    assert "not saved" in out and "ran" in out, "the output is still reported to the agent"


def test_a_cell_turned_into_markdown_during_the_run_gets_no_outputs(ws, monkeypatch):
    _write([_code("slow()", "slow")])

    def to_markdown(cells):
        cells[0] = _md("now prose", "slow")
    out = _run(monkeypatch, _edit(to_markdown))
    cells = _read()
    assert cells[0].cell_type == "markdown" and "outputs" not in cells[0]
    assert "not saved" in out


def test_the_cells_own_source_edited_during_the_run_keeps_the_edit(ws, monkeypatch):
    # JupyterLab does the same: outputs land in the cell, and the edit stays.
    _write([_code("slow()", "slow")])

    def edit(cells):
        cells[0].source = "slow()  # tweaked"
    out = _run(monkeypatch, _edit(edit))
    cell = _read()[0]
    assert cell.source == "slow()  # tweaked"
    assert [o.get("text") for o in cell.outputs] == ["ran\n"]
    assert "edited while it ran" in out


def test_a_notebook_deleted_during_the_run_is_not_recreated(ws, monkeypatch):
    _write([_code("slow()", "slow")])
    out = _run(monkeypatch, lambda: (ws / "nb.ipynb").unlink())
    assert not (ws / "nb.ipynb").exists()
    assert "deleted or renamed" in out


def test_negative_index_reports_the_resolved_index(ws, monkeypatch):
    _write([_md("intro", "i"), _code("slow()", "slow")])
    out = _run(monkeypatch, lambda: None, index=-1)
    assert out.startswith("Executed cell [1] (In[3])")
    assert [o.get("text") for o in _read()[1].outputs] == ["ran\n"]


# ── notebooks without cell ids (nbformat 4.4) ─────────────────────────


def test_without_ids_a_cell_added_below_survives(ws, monkeypatch):
    _write([_code("slow()")], minor=4)

    def add(cells):
        note = _md("USER NOTE")
        note.pop("id", None)
        cells.append(note)
    _run(monkeypatch, _edit(add))
    cells = _read()
    assert [c.source for c in cells] == ["slow()", "USER NOTE"]
    assert [o.get("text") for o in cells[0].outputs] == ["ran\n"]


def test_without_ids_the_cell_is_found_by_its_source_after_a_move(ws, monkeypatch):
    _write([_code("slow()")], minor=4)

    def insert_above(cells):
        title = _md("Title")
        title.pop("id", None)
        cells.insert(0, title)
    out = _run(monkeypatch, _edit(insert_above))
    assert [o.get("text") for o in _read()[1].outputs] == ["ran\n"]
    assert out.startswith("Executed cell [1]")


def test_without_ids_an_ambiguous_match_is_not_guessed(ws, monkeypatch):
    _write([_code("slow()")], minor=4)

    def duplicate_above(cells):
        a, b = _md("Title"), _code("slow()")
        a.pop("id", None)
        b.pop("id", None)
        cells.insert(0, b)
        cells.insert(0, a)
    out = _run(monkeypatch, _edit(duplicate_above))
    assert all(not c.get("outputs") for c in _read()), "outputs were written to a guessed cell"
    assert "not saved" in out
