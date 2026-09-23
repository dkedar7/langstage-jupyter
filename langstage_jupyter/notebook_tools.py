"""Notebook tools the agent uses to write, edit and execute cells.

Split out of ``agent.py`` so the tools can be imported (and unit-tested) without
constructing a chat model / agent — which needed an API key just to reach them.

Every tool goes through exactly two primitives:

* :func:`_load_notebook` — read a notebook
* :func:`_save_notebook` — write a notebook

Both resolve the path through the **Jupyter Server contents API**, so the server's
``root_dir`` is the single source of truth. Previously reads used
``nbformat.read()`` (resolved against the *agent process's cwd*) while writes used
the contents API (resolved against the *server's root_dir*): whenever those two
differed — precisely the documented manual-config flow — the agent read one file
and wrote another, edits vanished, and ``execute_cell`` crashed with a raw
``IndexError``. Both primitives fall back to the local filesystem together, so the
read and the write can never disagree about which file they mean.

That fallback is ONLY for an unreachable server (a connection error). A server
that answers is the authority even when it refuses: a 401/403 (wrong or stale
token) or a 5xx surfaces as a tool error instead of quietly redirecting the read
or write to the agent process's cwd (gh #125). And every path is confined to the
serving root before any of this runs (gh #117).
"""
from __future__ import annotations

import functools
import os
import queue
import re
import time
from typing import Annotated, Optional

import nbformat
import requests
from jupyter_client import BlockingKernelClient, find_connection_file

from langstage_jupyter import config

JUPYTER_SERVER_URL = config.JUPYTER_SERVER_URL
JUPYTER_TOKEN = config.JUPYTER_TOKEN
EXECUTE_TIMEOUT = config.EXECUTE_TIMEOUT

#: Seconds to wait for a freshly started kernel to answer before executing.
KERNEL_READY_TIMEOUT = 60.0

#: HTTP timeout for contents/session API calls.
_HTTP_TIMEOUT = 30.0

#: Seconds to wait, after interrupting a timed-out cell, for the kernel to go idle.
_INTERRUPT_GRACE = 10.0

#: notebook_path -> BlockingKernelClient (validated for liveness before reuse).
kernel_clients: dict[str, BlockingKernelClient] = {}


class NotebookNotFound(Exception):
    """The notebook doesn't exist (in the server's root, or on disk)."""


class NotebookToolError(Exception):
    """A failure a tool reports to the agent as an ``Error: ...`` string."""


class PathOutsideRoot(NotebookToolError):
    """The path would resolve outside the JupyterLab serving root (gh #117)."""


class ServerRefused(NotebookToolError):
    """The server answered, but not with success: auth failure, 5xx, ... (gh #125)."""


def _tool_errors(fn):
    """Turn a :class:`NotebookToolError` raised anywhere in a tool into its
    ``Error: ...`` string, keeping the "strings, never raw exceptions" contract
    without a try/except around every primitive call."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except NotebookToolError as e:
            return f"Error: {e}"

    return wrapper


# ── the two I/O primitives ───────────────────────────────────────────


def _headers() -> dict:
    return {"Authorization": f"token {JUPYTER_TOKEN}"} if JUPYTER_TOKEN else {}


def _norm(notebook_path: str) -> str:
    return notebook_path.strip("/")


def _confine(notebook_path: str) -> str:
    """Normalize a tool's ``notebook_path`` and refuse one that leaves the serving root.

    A ``..`` segment used to escape it: ``requests`` collapsed ``/api/contents/../x``
    to a URL the server 404s, and the disk fallback then honored the ``..`` against
    the process cwd — so ``create_notebook("../x.ipynb")`` wrote outside the root and
    reported success (gh #117). Any ``..`` segment (with ``/`` or ``\\``) and any
    drive-qualified or UNC path is refused before the server or the disk is touched.
    A leading ``/`` is kept as root-relative, the contents API's own convention.
    """
    path = _norm(notebook_path)
    if (
        ".." in re.split(r"[\\/]", path)
        or re.match(r"^[A-Za-z]:", path)
        or path.startswith("\\")
    ):
        raise PathOutsideRoot(
            f"{notebook_path!r} points outside the JupyterLab serving root. Use a path "
            "relative to the root, without '..' segments or a drive/UNC prefix."
        )
    return path


def _contents_url(notebook_path: str) -> str:
    return f"{JUPYTER_SERVER_URL}/api/contents/{notebook_path}"


def _refused(resp, action: str, notebook_path: str) -> ServerRefused:
    """The error for a response that is neither success nor a plain 404."""
    hint = ""
    if resp.status_code in (401, 403):
        hint = (
            " The token was rejected: check that LANGSTAGE_JUPYTER_TOKEN matches the "
            "running server's token (`langstage-jupyter --check-connection` verifies it)."
        )
    return ServerRefused(
        f"the Jupyter server at {JUPYTER_SERVER_URL} returned HTTP {resp.status_code} "
        f"for {action} {notebook_path!r}; nothing was read from or written to local "
        f"disk instead.{hint}"
    )


def _unreachable(e: requests.RequestException) -> bool:
    """Is this the one failure the disk fallback exists for: no server listening?

    Anything else (a read timeout, an invalid URL, ...) means the server is there or
    the config is wrong, and quietly using the local disk would split the files."""
    return isinstance(e, requests.ConnectionError)


def _request_failed(e: requests.RequestException, action: str, notebook_path: str):
    return ServerRefused(
        f"request to the Jupyter server at {JUPYTER_SERVER_URL} failed for {action} "
        f"{notebook_path!r}: {e}"
    )


def _load_notebook(notebook_path: str) -> nbformat.NotebookNode:
    """Read a notebook from the Jupyter server (falling back to disk if the
    server is unreachable). Raises :class:`NotebookNotFound` if it doesn't exist,
    :class:`ServerRefused` if the server answers with any other failure."""
    try:
        # NB: only `type` here. The contents API's `format` accepts text/base64 (for
        # files); sending format=json for a notebook makes it reject the request, and
        # we'd silently fall through to the filesystem — the very split we're fixing.
        resp = requests.get(
            _contents_url(notebook_path),
            headers=_headers(),
            params={"type": "notebook"},
            timeout=_HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        if not _unreachable(e):
            raise _request_failed(e, "reading", notebook_path) from e
        resp = None  # server unreachable → fall through to the filesystem

    if resp is not None:
        if resp.status_code == 200:
            return nbformat.from_dict(resp.json()["content"])
        if resp.status_code == 404:
            raise NotebookNotFound(notebook_path)
        raise _refused(resp, "reading", notebook_path)

    try:
        return nbformat.read(notebook_path, as_version=4)
    except FileNotFoundError as e:
        raise NotebookNotFound(notebook_path) from e


def _ensure_parent_dirs(notebook_path: str) -> None:
    """Best-effort ``mkdir -p`` for a notebook's parent directory (gh #97).

    ``create_notebook("reports/summary.ipynb")`` must work even when ``reports/``
    doesn't exist yet — the toolset ships no separate folder tool, so if the parent
    is missing the agent is otherwise stuck (the contents-API ``PUT`` 500s and the
    filesystem fallback ``nbformat.write`` raises an uncaught ``FileNotFoundError``).
    Walk each ancestor segment and create it through the server contents API (so the
    server's ``root_dir`` stays the single authority), falling back to
    ``os.makedirs`` on disk when the server is unreachable — the same server-first /
    disk-fallback shape as the two I/O primitives above."""
    parent = _norm(notebook_path).rpartition("/")[0]
    if not parent:
        return  # a bare filename — no parent to create

    segments = parent.split("/")
    try:
        built = ""
        for seg in segments:
            built = f"{built}/{seg}" if built else seg
            resp = requests.put(
                _contents_url(built),
                headers=_headers(),
                json={"type": "directory"},
                timeout=_HTTP_TIMEOUT,
            )
            # A directory PUT is idempotent (an existing dir returns 200/201). Any
            # other status is the server refusing: surface it, never create the
            # directory on local disk behind its back (gh #125).
            if resp.status_code not in (200, 201):
                raise _refused(resp, "creating directory", built)
    except requests.RequestException as e:
        if not _unreachable(e):
            raise _request_failed(e, "creating directory", parent) from e
        os.makedirs(parent, exist_ok=True)


def _save_notebook(nb: nbformat.NotebookNode, notebook_path: str) -> None:
    """Write a notebook back through the same authority :func:`_load_notebook`
    read it from — the server first (which also keeps the open tab in sync and
    preserves scroll position), then disk, only if the server is unreachable."""
    try:
        resp = requests.put(
            _contents_url(notebook_path),
            headers=_headers(),
            json={"type": "notebook", "format": "json", "content": nb},
            timeout=_HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        if not _unreachable(e):
            raise _request_failed(e, "writing", notebook_path) from e
    else:
        if resp.status_code in (200, 201):
            return
        raise _refused(resp, "writing", notebook_path)
    # Ensure the parent exists so a notebook in a not-yet-created subdirectory writes
    # cleanly instead of raising an uncaught FileNotFoundError (gh #97). No-op for a
    # bare filename or an already-present dir.
    parent = os.path.dirname(notebook_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    nbformat.write(nb, notebook_path)


def _notebook_exists(notebook_path: str) -> bool:
    try:
        resp = requests.get(
            _contents_url(notebook_path), headers=_headers(), timeout=_HTTP_TIMEOUT
        )
    except requests.RequestException as e:
        if not _unreachable(e):
            raise _request_failed(e, "checking", notebook_path) from e
        return os.path.exists(notebook_path)
    if resp.status_code == 200:
        return True
    if resp.status_code == 404:
        return False
    raise _refused(resp, "checking", notebook_path)


def _check_index(nb: nbformat.NotebookNode, cell_index: int) -> Optional[str]:
    """Return an actionable error string if ``cell_index`` isn't addressable."""
    n = len(nb.cells)
    if n == 0:
        return "Error: Notebook has no cells."
    if cell_index < -n or cell_index >= n:
        return f"Error: Cell index {cell_index} out of range (0-{n - 1})"
    return None


# ── kernel ───────────────────────────────────────────────────────────


def start_notebook_kernel(notebook_path: str) -> str:
    """Start (or reuse) the kernel session for a notebook; returns its kernel id."""
    try:
        return get_notebook_kernel_id(notebook_path)
    except ValueError:
        pass

    response = requests.post(
        f"{JUPYTER_SERVER_URL}/api/sessions",
        headers=_headers(),
        json={"path": notebook_path, "type": "notebook", "kernel": {"name": "python3"}},
        timeout=_HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 201):
        raise ValueError(f"Failed to start kernel for {notebook_path}: {response.text}")
    return response.json()["kernel"]["id"]


def get_notebook_kernel_id(notebook_path: str) -> str:
    """Kernel id of the notebook's running session, else ValueError."""
    response = requests.get(
        f"{JUPYTER_SERVER_URL}/api/sessions", headers=_headers(), timeout=_HTTP_TIMEOUT
    )
    if response.status_code != 200:
        raise ValueError(f"Cannot connect to Jupyter server at {JUPYTER_SERVER_URL}")
    # Match the session by EXACT normalized path, not substring containment. The old
    # ``notebook_path in session[...]["path"]`` test is Python substring containment,
    # so a short name that is a suffix of a longer notebook wrongly matched its
    # session — ``"a.ipynb" in "data.ipynb"`` is True — and execute_cell("a.ipynb")
    # ran in data.ipynb's kernel, silently sharing/clobbering its namespace while
    # saving outputs back into a.ipynb (gh #96). Both sides are normalized the same
    # way so the comparison is on the real path, not raw containment.
    target = _norm(notebook_path)
    for session in response.json():
        if _norm(session.get("notebook", {}).get("path", "")) == target:
            return session["kernel"]["id"]
    raise ValueError(f"No running kernel found for {notebook_path}")


def _await_ready(client: BlockingKernelClient, timeout: float) -> None:
    """Block until the kernel actually answers, then drain iopub.

    We do the handshake ourselves rather than calling ``wait_for_ready()``, which
    gates on ``is_alive()`` — that reads the heartbeat channel, and on a client we
    just attached to a *pre-existing* kernel the heartbeat isn't beating yet, so
    it reports the kernel dead and raises. A ``kernel_info`` round-trip on the
    shell channel is what really proves the channels are up; draining iopub
    afterwards ensures the SUB subscription is live, so the next execute's output
    can't be dropped (ZMQ slow-joiner).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        client.kernel_info()
        try:
            msg = client.get_shell_msg(timeout=1.0)
        except queue.Empty:
            continue
        if msg["header"]["msg_type"] == "kernel_info_reply":
            while True:  # flush anything already queued on iopub
                try:
                    client.get_iopub_msg(timeout=0.2)
                except queue.Empty:
                    break
            return
    raise RuntimeError(f"kernel did not become ready within {timeout}s")


def _connect_kernel(notebook_path: str) -> BlockingKernelClient:
    """Return a *ready* kernel client for the notebook, reusing a live cached one.

    Two fixes live here:

    * readiness — the old code called ``start_channels()`` and executed immediately.
      The iopub SUB subscription isn't established synchronously (ZMQ slow-joiner),
      so a cold kernel's ``execute_input`` / ``stream`` / ``idle`` messages could be
      dropped: the tool then saw *no* output, spun for the whole ``EXECUTE_TIMEOUT``
      (300s by default) and reported a false timeout with ``execution_count=None`` —
      even though the kernel had really run the code. :func:`_await_ready` forces a
      round-trip first.
    * liveness — the cache was never invalidated, so restarting the kernel from the
      JupyterLab UI left a dead client behind and every later execute hung.
    """
    client = kernel_clients.get(notebook_path)
    if client is not None:
        try:
            if client.is_alive():
                return client
        except Exception:  # noqa: BLE001 - a dead client can fail in many ways
            pass
        try:
            client.stop_channels()
        except Exception:  # noqa: BLE001
            pass
        kernel_clients.pop(notebook_path, None)

    kernel_id = start_notebook_kernel(notebook_path)
    client = BlockingKernelClient()
    client.load_connection_file(find_connection_file(kernel_id))
    client.start_channels()
    _await_ready(client, KERNEL_READY_TIMEOUT)
    kernel_clients[notebook_path] = client
    return client


# ── tools ────────────────────────────────────────────────────────────


@_tool_errors
def get_notebook_state(notebook_path: Annotated[str, "Notebook filename"]) -> str:
    """Summarize a notebook: cell count, which cells ran, and where to insert next.

    Includes a one-line preview of each cell's source so you can see what's already
    there without guessing.
    """
    notebook_path = _confine(notebook_path)
    try:
        nb = _load_notebook(notebook_path)
    except NotebookNotFound:
        return f"Error: Notebook not found at {notebook_path}"

    executed, unexecuted, previews = [], [], []
    for idx, cell in enumerate(nb.cells):
        first = (cell.source or "").splitlines()[0] if (cell.source or "").strip() else ""
        previews.append(f"  [{idx}] ({cell.cell_type}) {first[:60]}")
        if cell.cell_type == "code":
            if cell.execution_count is not None:
                executed.append(f"[{idx}] (count: {cell.execution_count})")
            else:
                unexecuted.append(f"[{idx}]")

    lines = [
        f"Notebook state for {notebook_path}:",
        f"- Total cells: {len(nb.cells)}",
        f"- Code cells: {sum(1 for c in nb.cells if c.cell_type == 'code')}",
        f"- Markdown cells: {sum(1 for c in nb.cells if c.cell_type == 'markdown')}",
        f"- Executed cells: {', '.join(executed) if executed else 'None'}",
        f"- Unexecuted code cells: {', '.join(unexecuted) if unexecuted else 'None'}",
        f"- Next insertion index: {len(nb.cells)} (end of notebook)",
    ]
    if previews:
        lines.append("- Cells:")
        lines.extend(previews)
    return "\n".join(lines)


@_tool_errors
def read_cell(
    notebook_path: Annotated[str, "Notebook filename"],
    cell_index: Annotated[int, "Index of the cell to read"],
) -> str:
    """Return the full source of one cell, so you can modify it accurately."""
    notebook_path = _confine(notebook_path)
    try:
        nb = _load_notebook(notebook_path)
    except NotebookNotFound:
        return f"Error: Notebook not found at {notebook_path}"
    err = _check_index(nb, cell_index)
    if err:
        return err
    cell = nb.cells[cell_index]
    return f"Cell [{cell_index}] ({cell.cell_type}) in {notebook_path}:\n{cell.source}"


@_tool_errors
def create_notebook(
    notebook_path: Annotated[str, "Notebook filename"],
    overwrite: Annotated[bool, "Replace an existing notebook, DESTROYING its cells"] = False,
) -> str:
    """Create a new empty notebook.

    Refuses if the notebook already exists — pass ``overwrite=True`` only when you
    really mean to throw its contents away. (It used to overwrite unconditionally
    and report success, silently destroying a user's work.)
    """
    notebook_path = _confine(notebook_path)
    if not overwrite and _notebook_exists(notebook_path):
        return (
            f"Notebook already exists at {notebook_path} — kept as-is (nothing was "
            "overwritten). Use it directly, or call create_notebook with "
            "overwrite=True to replace it, which DESTROYS its existing cells."
        )
    # mkdir -p the parent so a notebook in a not-yet-created subdirectory is created
    # instead of crashing with an uncaught FileNotFoundError — there's no separate
    # folder tool, so this is the only way the agent can organize notebooks under a
    # subdir (gh #97). Never raises: return a clean Error string if the write fails.
    try:
        _ensure_parent_dirs(notebook_path)
        _save_notebook(nbformat.v4.new_notebook(), notebook_path)
    except OSError as e:
        return f"Error: could not create notebook at {notebook_path}: {e}"
    return f"Created new notebook at {notebook_path}"


@_tool_errors
def insert_code_cell(
    code: Annotated[str, "Python code for the cell"],
    notebook_path: Annotated[str, "Notebook filename"],
    cell_index: Annotated[int, "Index to insert at (-1 appends at the end)"] = -1,
) -> str:
    """Insert a new code cell into an existing notebook."""
    notebook_path = _confine(notebook_path)
    try:
        nb = _load_notebook(notebook_path)
    except NotebookNotFound:
        return (
            f"Error: Notebook not found at {notebook_path}. "
            "Create it first with create_notebook."
        )

    new_cell = nbformat.v4.new_code_cell(source=code)
    if cell_index == -1:
        nb.cells.append(new_cell)
        cell_index = len(nb.cells) - 1
    else:
        if cell_index < 0 or cell_index > len(nb.cells):
            return f"Error: Cell index {cell_index} out of range (0-{len(nb.cells)})"
        nb.cells.insert(cell_index, new_cell)

    _save_notebook(nb, notebook_path)
    return f"Inserted code cell at index {cell_index} in {notebook_path}"


@_tool_errors
def insert_markdown_cell(
    text: Annotated[str, "Markdown text for the cell"],
    notebook_path: Annotated[str, "Notebook filename"],
    cell_index: Annotated[int, "Index to insert at (-1 appends at the end)"] = -1,
) -> str:
    """Insert a new markdown cell into an existing notebook.

    The markdown twin of :func:`insert_code_cell` — for titles, section headers,
    and the narrative prose that a notebook interleaves with its code. Markdown
    cells are never executed, so there's no execute step after inserting one.
    """
    notebook_path = _confine(notebook_path)
    try:
        nb = _load_notebook(notebook_path)
    except NotebookNotFound:
        return (
            f"Error: Notebook not found at {notebook_path}. "
            "Create it first with create_notebook."
        )

    new_cell = nbformat.v4.new_markdown_cell(source=text)
    if cell_index == -1:
        nb.cells.append(new_cell)
        cell_index = len(nb.cells) - 1
    else:
        if cell_index < 0 or cell_index > len(nb.cells):
            return f"Error: Cell index {cell_index} out of range (0-{len(nb.cells)})"
        nb.cells.insert(cell_index, new_cell)

    _save_notebook(nb, notebook_path)
    return f"Inserted markdown cell at index {cell_index} in {notebook_path}"


@_tool_errors
def modify_cell(
    notebook_path: Annotated[str, "Notebook filename"],
    cell_index: Annotated[int, "Index of cell to modify"],
    new_code: Annotated[str, "New source for the cell (code or markdown)"],
) -> str:
    """Replace a cell's source. Works on **code and markdown** cells alike.

    For a code cell, the stale outputs/execution count are cleared. A markdown
    cell has neither, so only its source is replaced (this is how you edit a
    title, header, or paragraph — gh #70).

    Does **not** delete: an empty ``new_code`` used to silently remove the cell.
    Use :func:`delete_cell` to remove one.
    """
    notebook_path = _confine(notebook_path)
    if new_code == "":
        return (
            "Error: modify_cell no longer deletes a cell when new_code is empty. "
            "Use delete_cell(notebook_path, cell_index) to remove it."
        )
    try:
        nb = _load_notebook(notebook_path)
    except NotebookNotFound:
        return f"Error: Notebook not found at {notebook_path}"
    err = _check_index(nb, cell_index)
    if err:
        return err

    cell = nb.cells[cell_index]
    cell.source = new_code
    # Only code cells carry outputs / an execution count to invalidate; a markdown
    # cell has neither, so the old code-only guard was the only thing blocking a
    # markdown edit (gh #70).
    if cell.cell_type == "code":
        cell.outputs = []
        cell.execution_count = None

    _save_notebook(nb, notebook_path)
    return f"Modified cell at index {cell_index} in {notebook_path}"


@_tool_errors
def delete_cell(
    notebook_path: Annotated[str, "Notebook filename"],
    cell_index: Annotated[int, "Index of cell to delete"],
) -> str:
    """Delete a cell from the notebook."""
    notebook_path = _confine(notebook_path)
    try:
        nb = _load_notebook(notebook_path)
    except NotebookNotFound:
        return f"Error: Notebook not found at {notebook_path}"
    err = _check_index(nb, cell_index)
    if err:
        return err

    nb.cells.pop(cell_index)
    _save_notebook(nb, notebook_path)
    return f"Deleted cell at index {cell_index} in {notebook_path}"


@_tool_errors
def execute_cell(
    notebook_path: Annotated[str, "Notebook filename"],
    cell_index: Annotated[int, "Index of cell to execute (-1 = last)"] = -1,
) -> str:
    """Execute a code cell in the notebook's kernel and store its outputs.

    Reuses the notebook's existing kernel session (so the agent shares state with
    the cells the user runs in the UI), starting one if needed.
    """
    notebook_path = _confine(notebook_path)
    try:
        nb = _load_notebook(notebook_path)
    except NotebookNotFound:
        return (
            f"Error: Notebook not found at {notebook_path}. "
            "Create it first with create_notebook."
        )
    err = _check_index(nb, cell_index)
    if err:
        return err

    cell = nb.cells[cell_index]
    if cell.cell_type != "code":
        return f"Error: Cell {cell_index} is not a code cell"

    try:
        client = _connect_kernel(notebook_path)
    except (ValueError, RuntimeError) as e:
        return f"Error: could not start/attach a kernel for {notebook_path}: {e}"

    # allow_stdin=False: nothing here services the stdin channel, so with the client's
    # default (True) an `input()` cell parked the kernel on an input_request for the
    # whole EXECUTE_TIMEOUT and then left it wedged. False makes the kernel raise
    # StdinNotImplementedError at once, as `nbconvert --execute` does (gh #128).
    msg_id = client.execute(cell.source, allow_stdin=False)

    run = _Run()
    finished = _collect(client, msg_id, run, time.monotonic() + EXECUTE_TIMEOUT)

    if not finished:
        budget = (
            f"[langstage-jupyter] Cell exceeded EXECUTE_TIMEOUT={EXECUTE_TIMEOUT}s. "
            "Set LANGSTAGE_EXECUTE_TIMEOUT to raise the budget."
        )
        if not run.started:
            # The kernel never started this cell: it is busy with another execution
            # (a long cell the user ran, say). Interrupting would kill THAT, so leave
            # the kernel alone, and leave the cell's existing outputs untouched: they
            # used to be overwritten with [] and execution_count None (gh #116).
            return (
                f"Error: cell {cell_index} in {notebook_path} did not start within "
                f"EXECUTE_TIMEOUT={EXECUTE_TIMEOUT}s because the kernel is busy with "
                "another execution. The kernel was not interrupted and the cell's "
                "existing outputs were kept. The request is still queued and may run "
                "once the kernel is free."
            )
        # Our cell is the one running: interrupt it so the kernel is free for the
        # next call. Without this every later execute queued behind the runaway and
        # "timed out" too, and an infinite loop wedged the kernel for good (gh #116).
        failure = _interrupt_kernel(notebook_path)
        if failure:
            budget += f" It could not interrupt the kernel ({failure}); it may still be busy."
        elif _collect(client, msg_id, run, time.monotonic() + _INTERRUPT_GRACE):
            budget += " The kernel was interrupted and is idle again."
        else:
            budget += (
                f" The kernel was interrupted but did not go idle within {_INTERRUPT_GRACE}s;"
                " it may need a restart from JupyterLab."
            )
        run.texts.append(budget + " Output above may be incomplete.")

    # A run that started replaces the cell's outputs with its own, partial ones
    # included (that is what running a cell means in JupyterLab too).
    cell.execution_count = run.execution_count
    cell.outputs = run.outputs
    _save_notebook(nb, notebook_path)

    summary = "\n".join(run.texts) if run.texts else "(no output)"
    return f"Executed cell [{run.execution_count}] in {notebook_path}:\n{summary}"


class _Run:
    """What one execution has produced so far."""

    def __init__(self):
        self.outputs: list = []
        self.texts: list[str] = []
        self.execution_count: Optional[int] = None
        self.started = False


def _collect(client, msg_id: str, run: _Run, deadline: float) -> bool:
    """Read iopub messages for ``msg_id`` into ``run`` until the kernel reports idle
    (returns True) or ``deadline`` passes (returns False)."""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            msg = client.get_iopub_msg(timeout=min(1.0, remaining))
        except queue.Empty:
            continue
        if msg["parent_header"].get("msg_id") != msg_id:
            continue

        msg_type, content = msg["header"]["msg_type"], msg["content"]
        if msg_type == "status" and content["execution_state"] == "idle":
            return True
        run.started = True
        if msg_type == "execute_input":
            run.execution_count = content["execution_count"]
        elif msg_type == "stream":
            run.outputs.append(nbformat.v4.new_output("stream", name=content["name"], text=content["text"]))
            run.texts.append(f"[{content['name']}] {content['text']}")
        elif msg_type == "execute_result":
            run.outputs.append(nbformat.v4.new_output(
                "execute_result", data=content["data"], execution_count=content["execution_count"]))
            run.texts.append(content["data"].get("text/plain", str(content["data"])))
        elif msg_type == "display_data":
            run.outputs.append(nbformat.v4.new_output("display_data", data=content["data"]))
            run.texts.append(f"[display] {content['data'].get('text/plain', 'Rich content')}")
        elif msg_type == "error":
            run.outputs.append(nbformat.v4.new_output(
                "error", ename=content["ename"], evalue=content["evalue"],
                traceback=content["traceback"]))
            run.texts.append(
                f"ERROR: {content['ename']}: {content['evalue']}\n" + "\n".join(content["traceback"]))


def _interrupt_kernel(notebook_path: str) -> Optional[str]:
    """Interrupt the notebook's kernel through the server; ``None`` on success, else
    a short reason. The REST call works for any kernel the server manages, local or
    not, which a connection-file client cannot do."""
    try:
        kernel_id = get_notebook_kernel_id(notebook_path)
        resp = requests.post(
            f"{JUPYTER_SERVER_URL}/api/kernels/{kernel_id}/interrupt",
            headers=_headers(),
            timeout=_HTTP_TIMEOUT,
        )
    except (ValueError, requests.RequestException) as e:
        return str(e)
    if resp.status_code not in (200, 204):
        return f"HTTP {resp.status_code}"
    return None


#: The notebook toolset handed to the agent.
NOTEBOOK_TOOLS = [
    get_notebook_state,
    read_cell,
    create_notebook,
    insert_code_cell,
    insert_markdown_cell,
    modify_cell,
    delete_cell,
    execute_cell,
]
