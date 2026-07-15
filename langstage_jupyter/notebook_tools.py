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
"""
from __future__ import annotations

import os
import queue
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

#: notebook_path -> BlockingKernelClient (validated for liveness before reuse).
kernel_clients: dict[str, BlockingKernelClient] = {}


class NotebookNotFound(Exception):
    """The notebook doesn't exist (in the server's root, or on disk)."""


# ── the two I/O primitives ───────────────────────────────────────────


def _headers() -> dict:
    return {"Authorization": f"token {JUPYTER_TOKEN}"} if JUPYTER_TOKEN else {}


def _norm(notebook_path: str) -> str:
    return notebook_path.strip("/")


def _contents_url(notebook_path: str) -> str:
    return f"{JUPYTER_SERVER_URL}/api/contents/{notebook_path}"


def _load_notebook(notebook_path: str) -> nbformat.NotebookNode:
    """Read a notebook from the Jupyter server (falling back to disk if the
    server is unreachable). Raises :class:`NotebookNotFound` if it doesn't exist."""
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
    except requests.RequestException:
        resp = None  # server unreachable → fall through to the filesystem

    if resp is not None:
        if resp.status_code == 200:
            return nbformat.from_dict(resp.json()["content"])
        if resp.status_code == 404:
            raise NotebookNotFound(notebook_path)

    try:
        return nbformat.read(notebook_path, as_version=4)
    except FileNotFoundError as e:
        raise NotebookNotFound(notebook_path) from e


def _save_notebook(nb: nbformat.NotebookNode, notebook_path: str) -> None:
    """Write a notebook back through the same authority :func:`_load_notebook`
    read it from — the server first (which also keeps the open tab in sync and
    preserves scroll position), then disk."""
    try:
        resp = requests.put(
            _contents_url(notebook_path),
            headers=_headers(),
            json={"type": "notebook", "format": "json", "content": nb},
            timeout=_HTTP_TIMEOUT,
        )
        if resp.status_code in (200, 201):
            return
    except requests.RequestException:
        pass
    nbformat.write(nb, notebook_path)


def _notebook_exists(notebook_path: str) -> bool:
    try:
        resp = requests.get(
            _contents_url(notebook_path), headers=_headers(), timeout=_HTTP_TIMEOUT
        )
        if resp.status_code == 200:
            return True
        if resp.status_code == 404:
            return False
    except requests.RequestException:
        pass
    return os.path.exists(notebook_path)


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
    for session in response.json():
        if notebook_path in session["notebook"]["path"]:
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


def get_notebook_state(notebook_path: Annotated[str, "Notebook filename"]) -> str:
    """Summarize a notebook: cell count, which cells ran, and where to insert next.

    Includes a one-line preview of each cell's source so you can see what's already
    there without guessing.
    """
    notebook_path = _norm(notebook_path)
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


def read_cell(
    notebook_path: Annotated[str, "Notebook filename"],
    cell_index: Annotated[int, "Index of the cell to read"],
) -> str:
    """Return the full source of one cell, so you can modify it accurately."""
    notebook_path = _norm(notebook_path)
    try:
        nb = _load_notebook(notebook_path)
    except NotebookNotFound:
        return f"Error: Notebook not found at {notebook_path}"
    err = _check_index(nb, cell_index)
    if err:
        return err
    cell = nb.cells[cell_index]
    return f"Cell [{cell_index}] ({cell.cell_type}) in {notebook_path}:\n{cell.source}"


def create_notebook(
    notebook_path: Annotated[str, "Notebook filename"],
    overwrite: Annotated[bool, "Replace an existing notebook, DESTROYING its cells"] = False,
) -> str:
    """Create a new empty notebook.

    Refuses if the notebook already exists — pass ``overwrite=True`` only when you
    really mean to throw its contents away. (It used to overwrite unconditionally
    and report success, silently destroying a user's work.)
    """
    notebook_path = _norm(notebook_path)
    if not overwrite and _notebook_exists(notebook_path):
        return (
            f"Notebook already exists at {notebook_path} — kept as-is (nothing was "
            "overwritten). Use it directly, or call create_notebook with "
            "overwrite=True to replace it, which DESTROYS its existing cells."
        )
    _save_notebook(nbformat.v4.new_notebook(), notebook_path)
    return f"Created new notebook at {notebook_path}"


def insert_code_cell(
    code: Annotated[str, "Python code for the cell"],
    notebook_path: Annotated[str, "Notebook filename"],
    cell_index: Annotated[int, "Index to insert at (-1 appends at the end)"] = -1,
) -> str:
    """Insert a new code cell into an existing notebook."""
    notebook_path = _norm(notebook_path)
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
    notebook_path = _norm(notebook_path)
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
    notebook_path = _norm(notebook_path)
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


def delete_cell(
    notebook_path: Annotated[str, "Notebook filename"],
    cell_index: Annotated[int, "Index of cell to delete"],
) -> str:
    """Delete a cell from the notebook."""
    notebook_path = _norm(notebook_path)
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


def execute_cell(
    notebook_path: Annotated[str, "Notebook filename"],
    cell_index: Annotated[int, "Index of cell to execute (-1 = last)"] = -1,
) -> str:
    """Execute a code cell in the notebook's kernel and store its outputs.

    Reuses the notebook's existing kernel session (so the agent shares state with
    the cells the user runs in the UI), starting one if needed.
    """
    notebook_path = _norm(notebook_path)
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

    msg_id = client.execute(cell.source)

    outputs, output_texts, execution_count = [], [], None
    deadline = time.monotonic() + EXECUTE_TIMEOUT
    timed_out = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        try:
            msg = client.get_iopub_msg(timeout=min(1.0, remaining))
        except queue.Empty:
            continue
        if msg["parent_header"].get("msg_id") != msg_id:
            continue

        msg_type, content = msg["header"]["msg_type"], msg["content"]
        if msg_type == "execute_input":
            execution_count = content["execution_count"]
        elif msg_type == "stream":
            outputs.append(nbformat.v4.new_output("stream", name=content["name"], text=content["text"]))
            output_texts.append(f"[{content['name']}] {content['text']}")
        elif msg_type == "execute_result":
            outputs.append(nbformat.v4.new_output(
                "execute_result", data=content["data"], execution_count=content["execution_count"]))
            output_texts.append(content["data"].get("text/plain", str(content["data"])))
        elif msg_type == "display_data":
            outputs.append(nbformat.v4.new_output("display_data", data=content["data"]))
            output_texts.append(f"[display] {content['data'].get('text/plain', 'Rich content')}")
        elif msg_type == "error":
            outputs.append(nbformat.v4.new_output(
                "error", ename=content["ename"], evalue=content["evalue"],
                traceback=content["traceback"]))
            output_texts.append(
                f"ERROR: {content['ename']}: {content['evalue']}\n" + "\n".join(content["traceback"]))
        elif msg_type == "status" and content["execution_state"] == "idle":
            break

    if timed_out:
        output_texts.append(
            f"[langstage-jupyter] Cell exceeded EXECUTE_TIMEOUT={EXECUTE_TIMEOUT}s; "
            "output above may be incomplete. Set LANGSTAGE_EXECUTE_TIMEOUT to raise the budget."
        )

    cell.execution_count = execution_count
    cell.outputs = outputs
    _save_notebook(nb, notebook_path)

    summary = "\n".join(output_texts) if output_texts else "(no output)"
    return f"Executed cell [{execution_count}] in {notebook_path}:\n{summary}"


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
