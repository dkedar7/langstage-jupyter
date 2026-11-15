"""gh #203: an absolute host path means the file it names, never a copy under the root.

With LANGSTAGE_VIRTUAL_MODE=false the agent's file tools speak absolute host paths
("/home/me/proj/analysis.ipynb"). The notebook tools read every leading "/" as
root-relative, so the path a file tool had just listed was "not found", and
create_notebook / insert_code_cell / execute_cell then created, edited and ran
<root>/home/me/proj/analysis.ipynb and reported success. The user's notebook never
changed. On Windows the same path ("C:\\...") was refused outright.
"""
import ntpath
import os
import posixpath

import nbformat
import pytest
import requests

from langstage_jupyter import config
from langstage_jupyter import notebook_tools as nt

BS = "\\"
POSIX = os.sep == "/"


@pytest.fixture
def direct(monkeypatch):
    """LANGSTAGE_VIRTUAL_MODE=false: the file tools hand out absolute host paths."""
    monkeypatch.setattr(config, "VIRTUAL_MODE", False)


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A serving root with one notebook in it, known to the tools."""
    served = tmp_path / "proj"
    served.mkdir()
    monkeypatch.setattr(nt, "_SERVING_ROOT", str(served))
    return served


@pytest.fixture
def offline(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("no server")

    for verb in ("get", "put", "post"):
        monkeypatch.setattr(nt.requests, verb, boom)


class _Contents:
    """An in-memory contents API: notebooks by root-relative path."""

    def __init__(self, monkeypatch):
        self.files = {}
        self.dirs = set()
        monkeypatch.setattr(nt.requests, "get", self.get)
        monkeypatch.setattr(nt.requests, "put", self.put)

    def _path(self, url):
        return url.split("/api/contents/", 1)[1]

    def get(self, url, **kw):
        class Resp:
            pass

        resp = Resp()
        if "/api/contents/" not in url:
            resp.status_code = 404
            return resp
        path = self._path(url)
        if path in self.files:
            resp.status_code = 200
            resp.json = lambda: {"content": self.files[path]}
        elif path in self.dirs:
            resp.status_code = 200
            resp.json = lambda: {"type": "directory"}
        else:
            resp.status_code = 404
        return resp

    def put(self, url, **kw):
        class Resp:
            status_code = 200
            text = ""

            def json(self):
                return {}

        body = kw.get("json") or {}
        if body.get("type") == "directory":
            self.dirs.add(self._path(url))
        else:
            self.files[self._path(url)] = nbformat.from_dict(body["content"])
        return Resp()


def _notebook(*sources):
    nb = nbformat.v4.new_notebook()
    nb.cells.extend(nbformat.v4.new_code_cell(source=s) for s in sources)
    return nb


# ── the path helpers, on both platforms' spellings ───────────────────


@pytest.mark.parametrize("raw, expected", [
    ("/home/me/proj/analysis.ipynb", "/home/me/proj/analysis.ipynb"),
    ("/analysis.ipynb", "/analysis.ipynb"),
    ("analysis.ipynb", None),
    ("./analysis.ipynb", None),
    ("sub/b.ipynb", None),
])
def test_posix_host_paths_are_the_ones_with_a_leading_slash(raw, expected):
    assert nt._host_path(raw, posixpath) == expected


@pytest.mark.parametrize("raw, expected", [
    (f"C:{BS}Users{BS}me{BS}proj{BS}a.ipynb", f"C:{BS}Users{BS}me{BS}proj{BS}a.ipynb"),
    ("C:/Users/me/proj/a.ipynb", f"C:{BS}Users{BS}me{BS}proj{BS}a.ipynb"),
    ("/C:/Users/me/proj/a.ipynb", f"C:{BS}Users{BS}me{BS}proj{BS}a.ipynb"),
    (f"{BS * 2}srv{BS}share{BS}a.ipynb", f"{BS * 2}srv{BS}share{BS}a.ipynb"),
    ("C:a.ipynb", None),         # drive-relative: not absolute
    ("/a.ipynb", None),          # no drive: the contents API's root-relative spelling
    ("//a.ipynb", None),         # ... in any of its spellings (gh #187), not a UNC host
    ("//srv/share/a.ipynb", None),
    (f"{BS}a.ipynb", None),
    ("a.ipynb", None),
    (f"sub{BS}b.ipynb", None),
])
def test_windows_host_paths_are_drive_qualified_or_unc(raw, expected):
    assert nt._host_path(raw, ntpath) == expected


@pytest.mark.parametrize("path, root_dir, expected", [
    ("/home/me/proj/analysis.ipynb", "/home/me/proj", "analysis.ipynb"),
    ("/home/me/proj/sub/b.ipynb", "/home/me/proj", "sub/b.ipynb"),
    ("/home/me/proj/sub/b.ipynb", "/home/me/proj/", "sub/b.ipynb"),
    ("/home/me/proj//sub/./b.ipynb", "/home/me/proj", "sub/b.ipynb"),
    ("/home/me/proj", "/home/me/proj", ""),
    ("/analysis.ipynb", "/", "analysis.ipynb"),
    ("/home/me/proj2/analysis.ipynb", "/home/me/proj", None),   # a sibling, not a child
    ("/home/me/analysis.ipynb", "/home/me/proj", None),
    ("/tmp/other/x.ipynb", "/home/me/proj", None),
    ("/home/me/PROJ/a.ipynb", "/home/me/proj", None),           # POSIX is case-sensitive
])
def test_under_posix(path, root_dir, expected):
    assert nt._under(path, root_dir, posixpath) == expected


@pytest.mark.parametrize("path, root_dir, expected", [
    (f"C:{BS}Users{BS}me{BS}proj{BS}a.ipynb", f"C:{BS}Users{BS}me{BS}proj", "a.ipynb"),
    (f"C:{BS}Users{BS}me{BS}proj{BS}sub{BS}b.ipynb", f"C:{BS}Users{BS}me{BS}proj{BS}", "sub/b.ipynb"),
    # Windows paths are case-insensitive; the notebook's own spelling is kept.
    (f"c:{BS}users{BS}ME{BS}Proj{BS}Sub{BS}A.ipynb", f"C:{BS}Users{BS}me{BS}proj", "Sub/A.ipynb"),
    ("C:/Users/me/proj/sub/b.ipynb", f"C:{BS}Users{BS}me{BS}proj", "sub/b.ipynb"),
    (f"D:{BS}Users{BS}me{BS}proj{BS}a.ipynb", f"C:{BS}Users{BS}me{BS}proj", None),
    (f"C:{BS}Users{BS}me{BS}proj2{BS}a.ipynb", f"C:{BS}Users{BS}me{BS}proj", None),
    (f"C:{BS}a.ipynb", f"C:{BS}", "a.ipynb"),
    (f"{BS * 2}srv{BS}share{BS}nb{BS}a.ipynb", f"{BS * 2}srv{BS}share{BS}nb", "a.ipynb"),
    (f"{BS * 2}srv{BS}other{BS}a.ipynb", f"{BS * 2}srv{BS}share", None),
])
def test_under_windows(path, root_dir, expected):
    assert nt._under(path, root_dir, ntpath) == expected


# ── direct mode: an absolute path is the file it names ───────────────


def test_an_absolute_path_inside_the_root_is_that_notebook(direct, root):
    assert nt._confine(str(root / "analysis.ipynb")) == "analysis.ipynb"
    assert nt._confine(str(root / "sub" / "deep" / "b.ipynb")) == "sub/deep/b.ipynb"
    assert nt._confine(f"  {root / 'My Notebook #3.ipynb'}  ") == "My Notebook #3.ipynb"


def test_a_relative_path_is_unchanged_in_direct_mode(direct, root):
    assert nt._confine("analysis.ipynb") == "analysis.ipynb"
    assert nt._confine("./sub/b.ipynb") == "sub/b.ipynb"


def test_an_absolute_path_outside_the_root_is_refused_not_re_rooted(direct, root):
    outside = root.parent / "elsewhere" / "x.ipynb"
    with pytest.raises(nt.PathOutsideRoot) as e:
        nt._confine(str(outside))
    assert str(root) in str(e.value) and "absolute path outside" in str(e.value)

    sibling = root.parent / "proj2" / "x.ipynb"   # shares the root's name as a prefix
    with pytest.raises(nt.PathOutsideRoot):
        nt._confine(str(sibling))


@pytest.mark.skipif(not POSIX, reason="a leading '/' is a host path only on POSIX")
def test_posix_direct_mode_never_reads_a_leading_slash_as_root_relative(direct, root):
    # The issue's path: listed by the file tools, then re-rooted by the notebook tools.
    with pytest.raises(nt.PathOutsideRoot):
        nt._confine("/tmp/dogj/repro/analysis.ipynb")
    with pytest.raises(nt.PathOutsideRoot):
        nt._confine("/analysis.ipynb")


@pytest.mark.skipif(not POSIX, reason="symlinked roots as on macOS /tmp")
def test_a_root_reached_through_a_symlink_still_contains_its_notebooks(direct, tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(nt, "_SERVING_ROOT", str(link))
    # The file tools report the resolved path; the server was started on the link.
    assert nt._confine(str(real / "a.ipynb")) == "a.ipynb"
    monkeypatch.setattr(nt, "_SERVING_ROOT", str(real))
    assert nt._confine(str(link / "sub" / "a.ipynb")) == "sub/a.ipynb"


@pytest.mark.skipif(POSIX, reason="Windows spellings")
def test_windows_spellings_of_a_path_inside_the_root(root, monkeypatch):
    target = root / "sub" / "a.ipynb"
    forward = str(target).replace(BS, "/")
    for mode in (True, False):   # a drive-qualified path is a host path in either mode
        monkeypatch.setattr(config, "VIRTUAL_MODE", mode)
        assert nt._confine(str(target)) == "sub/a.ipynb"
        assert nt._confine(forward) == "sub/a.ipynb"
        assert nt._confine("/" + forward) == "sub/a.ipynb"
        assert nt._confine(str(target).swapcase()) == "sub/a.ipynb".swapcase()
        # No drive: still the contents API's root-relative spelling.
        assert nt._confine("/sub/a.ipynb") == "sub/a.ipynb"
        with pytest.raises(nt.PathOutsideRoot):
            nt._confine(str(root.parent / "elsewhere" / "a.ipynb"))
        with pytest.raises(nt.PathOutsideRoot):
            nt._confine("C:a.ipynb")
        with pytest.raises(nt.PathOutsideRoot):
            nt._confine(f"{BS * 2}host{BS}share{BS}a.ipynb")


# ── the issue, end to end against a contents API ─────────────────────


def test_tools_given_the_file_tools_path_use_the_users_notebook(direct, root, monkeypatch):
    server = _Contents(monkeypatch)
    server.files["analysis.ipynb"] = _notebook("x = 1")
    listed = str(root / "analysis.ipynb")          # what `ls` returns in direct mode

    state = nt.get_notebook_state(listed)
    assert "not found" not in state.lower() and "x = 1" in state

    created = nt.create_notebook(listed)
    assert "already exists" in created                 # not "Created ... <nested copy>"

    inserted = nt.insert_code_cell("print('hi')", listed)
    assert "Inserted code cell at index 1 in analysis.ipynb" in inserted

    assert list(server.files) == ["analysis.ipynb"], "no nested copy on the server"
    assert [c.source for c in server.files["analysis.ipynb"].cells] == ["x = 1", "print('hi')"]
    assert nt.read_cell(listed, 1).endswith("print('hi')")


def test_a_new_notebook_at_an_absolute_path_lands_in_the_root(direct, root, monkeypatch):
    server = _Contents(monkeypatch)
    out = nt.create_notebook(str(root / "reports" / "summary.ipynb"))
    assert out == "Created new notebook at reports/summary.ipynb"
    assert list(server.files) == ["reports/summary.ipynb"]
    assert server.dirs == {"reports"}


@pytest.mark.parametrize("tool", [
    lambda p: nt.get_notebook_state(p),
    lambda p: nt.create_notebook(p),
    lambda p: nt.insert_code_cell("x = 1", p),
    lambda p: nt.insert_markdown_cell("# t", p),
    lambda p: nt.modify_cell(p, 0, "x = 2"),
    lambda p: nt.delete_cell(p, 0),
    lambda p: nt.read_cell(p, 0),
    lambda p: nt.execute_cell(p, 0),
])
def test_every_tool_refuses_an_absolute_path_outside_the_root(direct, root, monkeypatch, tool):
    server = _Contents(monkeypatch)
    posted = []
    monkeypatch.setattr(nt.requests, "post", lambda *a, **k: posted.append(a))
    out = tool(str(root.parent / "elsewhere" / "x.ipynb"))
    assert out.startswith("Error:") and "outside the JupyterLab serving root" in out
    assert not server.files and not server.dirs and not posted, "nothing was touched"


def test_disk_fallback_edits_the_named_file_and_makes_no_nested_copy(direct, offline, tmp_path, monkeypatch):
    # No server: the root is the process cwd, where the disk fallback works.
    monkeypatch.setattr(nt, "_SERVING_ROOT", None)
    monkeypatch.chdir(tmp_path)
    nbformat.write(_notebook("x = 1"), str(tmp_path / "analysis.ipynb"))
    listed = str(tmp_path / "analysis.ipynb")

    assert "x = 1" in nt.get_notebook_state(listed)
    assert "Inserted code cell at index 1" in nt.insert_code_cell("y = 2", listed)

    cells = nbformat.read(str(tmp_path / "analysis.ipynb"), as_version=4).cells
    assert [c.source for c in cells] == ["x = 1", "y = 2"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["analysis.ipynb"], "no nested copy"

    out = nt.create_notebook(str(tmp_path.parent / "stray.ipynb"))
    assert out.startswith("Error:") and not (tmp_path.parent / "stray.ipynb").exists()


# ── virtual mode keeps the contents API's convention ─────────────────


def test_virtual_mode_keeps_a_leading_slash_root_relative(root):
    assert config.VIRTUAL_MODE is True
    assert nt._confine("/analysis.ipynb") == "analysis.ipynb"
    assert nt._confine("/sub/b.ipynb") == "sub/b.ipynb"


@pytest.mark.skipif(not POSIX, reason="a leading '/' is a host path only on POSIX")
def test_virtual_mode_maps_a_plain_host_path_from_a_direct_backend(root):
    # A custom agent with a direct backend while the config still says virtual: the
    # host path is inside the root and its first segment names nothing under it.
    assert nt._confine(str(root / "analysis.ipynb")) == "analysis.ipynb"
    assert nt._confine(str(root / "sub" / "b.ipynb")) == "sub/b.ipynb"


@pytest.mark.skipif(not POSIX, reason="a leading '/' is a host path only on POSIX")
def test_virtual_mode_prefers_the_root_relative_reading_when_it_names_something(root):
    first = str(root).lstrip("/").split("/", 1)[0]
    (root / first).mkdir()                       # <root>/<first segment> exists
    assert nt._confine(str(root / "a.ipynb")) == str(root / "a.ipynb").lstrip("/")


@pytest.mark.skipif(not POSIX, reason="a leading '/' is a host path only on POSIX")
def test_virtual_mode_without_a_known_root_is_unchanged(monkeypatch):
    monkeypatch.setattr(nt, "_SERVING_ROOT", None)
    called = []
    monkeypatch.setattr(nt.requests, "get", lambda *a, **k: called.append(a))
    assert nt._confine("/home/me/proj/a.ipynb") == "home/me/proj/a.ipynb"
    assert not called, "virtual mode never asks the server for its root"


# ── learning the serving root ────────────────────────────────────────


def _health(monkeypatch, status=200, body=None, calls=None):
    class Resp:
        status_code = status

        def json(self):
            if body is None:
                raise ValueError("not json")
            return body

    def get(url, **kw):
        if calls is not None:
            calls.append(url)
        return Resp()

    monkeypatch.setattr(nt.requests, "get", get)


def test_the_recorded_root_wins_and_needs_no_request(root, monkeypatch):
    monkeypatch.setattr(nt.requests, "get", lambda *a, **k: pytest.fail("no request expected"))
    assert nt._serving_root() == str(root)


def test_another_process_asks_the_server_once(tmp_path, monkeypatch):
    monkeypatch.setattr(nt, "_SERVING_ROOT", None)
    calls = []
    _health(monkeypatch, body={"status": "healthy", "serving_root": str(tmp_path)}, calls=calls)
    assert nt._serving_root() == str(tmp_path)
    assert nt._serving_root() == str(tmp_path)
    assert len(calls) == 1 and calls[0].endswith("/langstage-jupyter/health")


@pytest.mark.parametrize("status, body", [
    (404, None),                       # a plain Jupyter server without the extension
    (200, {"status": "healthy"}),      # an older extension
    (200, None),                       # not JSON
    (200, {"serving_root": ""}),
    (200, {"serving_root": 5}),
    (403, {"message": "Forbidden"}),
])
def test_a_server_that_does_not_report_its_root_gets_a_refusal_not_a_guess(direct, monkeypatch, tmp_path, status, body):
    monkeypatch.setattr(nt, "_SERVING_ROOT", None)
    monkeypatch.chdir(tmp_path)
    _health(monkeypatch, status=status, body=body)
    assert nt._serving_root() is None
    with pytest.raises(nt.PathOutsideRoot) as e:
        nt._confine(str(tmp_path / "a.ipynb"))     # even though it is under the cwd
    assert "did not report" in str(e.value)


def test_no_server_listening_means_the_cwd(offline, tmp_path, monkeypatch):
    monkeypatch.setattr(nt, "_SERVING_ROOT", None)
    monkeypatch.chdir(tmp_path)
    assert os.path.samefile(nt._serving_root(), tmp_path)


def test_a_timeout_is_not_mistaken_for_no_server(monkeypatch):
    monkeypatch.setattr(nt, "_SERVING_ROOT", None)

    def slow(*a, **k):
        raise requests.Timeout("slow")

    monkeypatch.setattr(nt.requests, "get", slow)
    assert nt._serving_root() is None


def test_the_server_extension_hands_its_root_to_the_tools(tmp_path, monkeypatch):
    from langstage_jupyter import agent_wrapper

    monkeypatch.setattr(agent_wrapper, "_SERVING_ROOT", None)
    monkeypatch.setattr(config, "WORKSPACE_ROOT", None)
    agent_wrapper.set_serving_root(str(tmp_path))
    assert nt._SERVING_ROOT == str(tmp_path)
    agent_wrapper.set_serving_root(None)
    assert nt._SERVING_ROOT is None


def test_a_home_abbreviated_root_is_expanded(direct, monkeypatch):
    # Jupyter Server's `server_root_dir` setting spells the home directory as "~".
    home = os.path.expanduser("~")
    nt.set_serving_root(os.path.join("~", "proj"))
    assert nt._SERVING_ROOT == os.path.join(home, "proj")
    assert nt._confine(os.path.join(home, "proj", "sub", "a.ipynb")) == "sub/a.ipynb"
    with pytest.raises(nt.PathOutsideRoot) as e:
        nt._confine(os.path.join(home, "elsewhere", "a.ipynb"))
    assert os.path.join(home, "proj") in str(e.value)


def test_health_reports_the_serving_root(tmp_path, monkeypatch):
    import asyncio
    import json

    from langstage_jupyter import handlers

    sent = []
    handler = handlers.HealthHandler.__new__(handlers.HealthHandler)
    monkeypatch.setattr(handlers, "_agent_readiness", lambda: ("healthy", True, "ok"))
    monkeypatch.setattr(
        handlers, "get_agent", lambda: type("A", (), {"agent": None})()
    )
    monkeypatch.setattr(
        handlers.HealthHandler, "settings", property(lambda self: {"server_root_dir": os.path.join("~", "proj")})
    )
    monkeypatch.setattr(handlers.HealthHandler, "finish", lambda self, chunk=None: sent.append(chunk))
    monkeypatch.setattr(handlers.HealthHandler, "current_user", property(lambda self: "me"))
    asyncio.run(handlers.HealthHandler.get.__wrapped__(handler))
    # The absolute directory, not the "~" spelling the setting holds: the tools asking
    # may run under another home.
    assert json.loads(sent[0])["serving_root"] == os.path.join(os.path.expanduser("~"), "proj")
