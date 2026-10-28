"""gh #187: equivalent spellings of one notebook path bind to one kernel session.

The contents API treats "./a.ipynb" as "a.ipynb", but the session match only
stripped "/": execute_cell("./a.ipynb") missed the user's open session, started a
second kernel, ran the cell there (a NameError on the user's variables) and saved
that output into the user's notebook.
"""
import os
import queue

import pytest

from langstage_jupyter import notebook_tools as nt


def _fake_sessions(monkeypatch, sessions):
    class Resp:
        status_code = 200

        def json(self):
            return sessions

    monkeypatch.setattr(nt.requests, "get", lambda *a, **k: Resp())


class _FakeClient:
    """Just enough of BlockingKernelClient for _connect_kernel's handshake."""

    def load_connection_file(self, cf):
        pass

    def start_channels(self):
        pass

    def kernel_info(self):
        pass

    def get_shell_msg(self, timeout=None):
        return {"header": {"msg_type": "kernel_info_reply"}}

    def get_iopub_msg(self, timeout=None):
        raise queue.Empty

    def is_alive(self):
        return True


@pytest.mark.parametrize("spelling, canonical", [
    ("a.ipynb", "a.ipynb"),
    ("./a.ipynb", "a.ipynb"),
    ("/a.ipynb", "a.ipynb"),
    (".//a.ipynb", "a.ipynb"),
    ("//a.ipynb", "a.ipynb"),
    (" a.ipynb ", "a.ipynb"),
    ("sub/./b.ipynb", "sub/b.ipynb"),
    ("sub//b.ipynb", "sub/b.ipynb"),
    ("./sub/b.ipynb/", "sub/b.ipynb"),
])
def test_confine_returns_one_spelling(spelling, canonical):
    assert nt._confine(spelling) == canonical


@pytest.mark.parametrize("spelling", ["./a.ipynb", "/a.ipynb", ".//a.ipynb", " a.ipynb "])
def test_kernel_lookup_matches_an_equivalent_spelling(monkeypatch, spelling):
    _fake_sessions(monkeypatch, [
        {"notebook": {"path": "data.ipynb"}, "kernel": {"id": "KID-DATA"}},
        {"notebook": {"path": "a.ipynb"}, "kernel": {"id": "KID-A"}},
    ])
    assert nt.get_notebook_kernel_id(spelling) == "KID-A"


def test_kernel_lookup_matches_a_session_saved_under_another_spelling(monkeypatch):
    # A session another client opened as "./sub/b.ipynb" is still sub/b.ipynb's.
    _fake_sessions(monkeypatch, [
        {"notebook": {"path": "./sub/b.ipynb"}, "kernel": {"id": "KID-B"}},
    ])
    assert nt.get_notebook_kernel_id("sub/b.ipynb") == "KID-B"


def test_a_new_session_is_posted_under_the_normalized_path(monkeypatch):
    posted = []

    class NoSessions:
        status_code = 200

        def json(self):
            return []

    class Created:
        status_code = 201

        def json(self):
            return {"kernel": {"id": "KID-NEW"}}

    monkeypatch.setattr(nt.requests, "get", lambda *a, **k: NoSessions())
    monkeypatch.setattr(
        nt.requests, "post", lambda url, **k: posted.append(k["json"]) or Created()
    )
    assert nt.start_notebook_kernel("./sub/./b.ipynb") == "KID-NEW"
    assert posted[0]["path"] == "sub/b.ipynb"


def test_equivalent_spellings_share_one_cached_client(monkeypatch):
    made = []
    monkeypatch.setattr(nt, "BlockingKernelClient", lambda: made.append(_FakeClient()) or made[-1])
    monkeypatch.setattr(nt, "start_notebook_kernel", lambda p: "kid")
    monkeypatch.setattr(nt, "find_connection_file", lambda k: "conn.json")
    nt.kernel_clients.clear()

    first = nt._connect_kernel(nt._confine("./a.ipynb"))
    assert nt._connect_kernel(nt._confine("a.ipynb")) is first
    assert len(made) == 1 and len(nt.kernel_clients) == 1
    nt.kernel_clients.clear()


BACKSLASH = "\\"


def test_case_and_backslashes_follow_the_platform(monkeypatch):
    # Windows paths are case-insensitive and take a backslash as a separator, so there
    # "A.ipynb" and "sub\b.ipynb" are the user's a.ipynb and sub/b.ipynb. On POSIX both
    # are different, legal file names and must not be folded together.
    _fake_sessions(monkeypatch, [
        {"notebook": {"path": "a.ipynb"}, "kernel": {"id": "KID-A"}},
        {"notebook": {"path": "sub/b.ipynb"}, "kernel": {"id": "KID-B"}},
    ])
    for spelling, kid in (("A.ipynb", "KID-A"), (f"sub{BACKSLASH}b.ipynb", "KID-B")):
        if os.name == "nt":
            assert nt.get_notebook_kernel_id(spelling) == kid
        else:
            with pytest.raises(ValueError):
                nt.get_notebook_kernel_id(spelling)


@pytest.mark.parametrize("path", [
    "sub/../a.ipynb",           # stays inside, but a `..` is refused outright
    " ../a.ipynb",              # surrounding whitespace doesn't hide a `..`
    "/C:/a.ipynb",              # a drive behind a leading slash
    f"/{BACKSLASH * 2}host{BACKSLASH}share{BACKSLASH}a.ipynb",  # UNC behind one
    f"sub{BACKSLASH}..{BACKSLASH}..{BACKSLASH}a.ipynb",
])
def test_normalizing_never_lets_a_refused_path_through(path):
    with pytest.raises(nt.PathOutsideRoot):
        nt._confine(path)
