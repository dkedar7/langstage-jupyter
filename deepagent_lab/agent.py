"""
Default agent configuration for deepagent-lab.

This agent is used when no custom agent is specified. It provides basic
notebook manipulation capabilities with filesystem access.
"""
import os
import queue
import time
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
load_dotenv()

from langgraph_stream_parser.demo import create_default_agent as _build_default_agent
from langchain.chat_models import init_chat_model

# Import configuration
from deepagent_lab import config

# Import notebook tools
from jupyter_client import BlockingKernelClient, find_connection_file
import nbformat
import requests

# Kernel clients cache
kernel_clients = {}

# === Configuration ===

# Get workspace root from environment or config
workspace_root = os.getenv('DEEPAGENT_WORKSPACE_ROOT')
if workspace_root:
    WORKSPACE = Path(workspace_root)
elif config.WORKSPACE_ROOT:
    WORKSPACE = config.WORKSPACE_ROOT
else:
    WORKSPACE = Path(".")

# Get Jupyter server configuration
JUPYTER_SERVER_URL = config.JUPYTER_SERVER_URL
JUPYTER_TOKEN = config.JUPYTER_TOKEN

# Model configuration
MODEL_NAME = config.MODEL_NAME
MODEL_TEMPERATURE = config.MODEL_TEMPERATURE

# Total seconds to wait for a single cell to finish executing before reporting
# possibly-incomplete output. Override via DEEPAGENT_EXECUTE_TIMEOUT.
EXECUTE_TIMEOUT = config.get_config("execute_timeout", default=300.0, type_cast=float)

# === Tool Definitions ===

def start_notebook_kernel(notebook_path: str) -> str:
    """
    Start a new kernel for a notebook via Jupyter Server API.

    Creates a new session with a running kernel for the specified notebook.
    If a session already exists for this notebook, returns the existing kernel ID.

    Args:
        notebook_path: Path to the notebook file (relative to server root).

    Returns:
        The kernel ID string for the running kernel.

    Raises:
        ValueError: If unable to connect to the Jupyter server or create a session.
    """
    # First check if a kernel is already running
    try:
        return get_notebook_kernel_id(notebook_path)
    except ValueError:
        pass  # No existing kernel, continue to start a new one

    # Start a new session with a kernel
    response = requests.post(
        f'{JUPYTER_SERVER_URL}/api/sessions',
        headers={'Authorization': f'token {JUPYTER_TOKEN}'} if JUPYTER_TOKEN else {},
        json={
            'path': notebook_path,
            'type': 'notebook',
            'kernel': {'name': 'python3'}
        }
    )

    if response.status_code not in [200, 201]:
        raise ValueError(f"Failed to start kernel for {notebook_path}: {response.text}")

    session = response.json()
    return session['kernel']['id']


def get_notebook_kernel_id(notebook_path: str) -> str:
    """
    Get the kernel ID for a running notebook via Jupyter Server API.

    Queries the Jupyter server's /api/sessions endpoint to find the active kernel
    associated with the specified notebook path.

    Args:
        notebook_path: Path to the notebook file (can be absolute or relative to server root).

    Returns:
        The kernel ID string for the running kernel.

    Raises:
        ValueError: If unable to connect to the Jupyter server or no running kernel
                   is found for the specified notebook.
    """
    response = requests.get(
        f'{JUPYTER_SERVER_URL}/api/sessions',
        headers={'Authorization': f'token {JUPYTER_TOKEN}'} if JUPYTER_TOKEN else {}
    )

    if response.status_code != 200:
        raise ValueError(f"Cannot connect to Jupyter server at {JUPYTER_SERVER_URL}")

    sessions = response.json()
    for session in sessions:
        if notebook_path in session['notebook']['path']:
            return session['kernel']['id']

    raise ValueError(f"No running kernel found for {notebook_path}")


def get_notebook_state(notebook_path: str) -> str:
    """
    Get the current state of a notebook including cell information.

    Reads the notebook and provides a summary of its current state, including
    the total number of cells, which cells have been executed, and the recommended
    index for inserting the next cell.

    Args:
        notebook_path: Path to the notebook file. Leading slashes are
                      automatically stripped.

    Returns:
        A formatted string containing:
        - Total number of cells
        - List of executed cells (with their execution counts)
        - List of unexecuted code cells
        - Recommended next insertion index (typically at the end)
        - Summary of cell types

    Example return:
        Notebook state for example.ipynb:
        - Total cells: 5
        - Code cells: 4
        - Markdown cells: 1
        - Executed cells: [0] (count: 1), [1] (count: 2)
        - Unexecuted cells: [2], [3]
        - Next insertion index: 5 (end of notebook)
    """
    notebook_path = notebook_path.strip("/")

    try:
        nb = nbformat.read(notebook_path, as_version=4)
    except FileNotFoundError:
        return f"Error: Notebook not found at {notebook_path}"

    total_cells = len(nb.cells)
    code_cells = sum(1 for cell in nb.cells if cell.cell_type == 'code')
    markdown_cells = sum(1 for cell in nb.cells if cell.cell_type == 'markdown')

    executed_cells = []
    unexecuted_cells = []

    for idx, cell in enumerate(nb.cells):
        if cell.cell_type == 'code':
            if cell.execution_count is not None:
                executed_cells.append(f"[{idx}] (count: {cell.execution_count})")
            else:
                unexecuted_cells.append(f"[{idx}]")

    # Next insertion index is typically at the end
    next_index = total_cells

    # Build the state summary
    state_lines = [
        f"Notebook state for {notebook_path}:",
        f"- Total cells: {total_cells}",
        f"- Code cells: {code_cells}",
        f"- Markdown cells: {markdown_cells}",
    ]

    if executed_cells:
        state_lines.append(f"- Executed cells: {', '.join(executed_cells)}")
    else:
        state_lines.append("- Executed cells: None")

    if unexecuted_cells:
        state_lines.append(f"- Unexecuted code cells: {', '.join(unexecuted_cells)}")
    else:
        state_lines.append("- Unexecuted code cells: None")

    state_lines.append(f"- Next insertion index: {next_index} (end of notebook)")

    return '\n'.join(state_lines)


def create_notebook(notebook_path: str) -> str:
    """
    Create a new empty Jupyter notebook file.

    Initializes a new notebook with nbformat v4 specification and writes it to the
    specified path. The notebook will contain no cells initially.

    Args:
        notebook_path: Path where the new notebook should be created. Leading slashes
                      are automatically stripped.

    Returns:
        A confirmation message indicating the notebook was created successfully,
        including the path to the new file.
    """

    notebook_path = notebook_path.strip("/")
    nb = nbformat.v4.new_notebook()
    nbformat.write(nb, notebook_path)
    return f"Created new notebook at {notebook_path}"


def insert_code_cell(
    code: Annotated[str, "Python code for the cell"],
    notebook_path: Annotated[str, "Notebook filename"],
    cell_idx: Annotated[int, "Index to insert cell at (-1 for append)"] = -1
) -> str:
    """
    Insert a new code cell into an existing notebook.

    Creates a new code cell with the specified source code and inserts it at the
    given position in the notebook. The notebook is saved either via the Jupyter
    Server API (to maintain scroll position) or directly to the filesystem if the
    API save fails.

    Args:
        code: The Python source code to insert into the new cell.
        notebook_path: Path to the target notebook file. Leading slashes are
                      automatically stripped.
        cell_idx: Zero-based index where the cell should be inserted. Use -1 to
                 append the cell at the end of the notebook. Defaults to -1.

    Returns:
        A confirmation message indicating the cell was inserted successfully,
        including the final index position and notebook path.
    """

    notebook_path = notebook_path.strip("/")

    nb = nbformat.read(notebook_path, as_version=4)

    new_cell = nbformat.v4.new_code_cell(source=code)
    # new_cell.metadata['jupyter'] = {'source_hidden': True}

    if cell_idx == -1:
        nb.cells.append(new_cell)
        cell_idx = len(nb.cells) - 1
    else:
        nb.cells.insert(cell_idx, new_cell)
        cell_idx = cell_idx

    # Save via API to maintain scroll position
    save_response = requests.put(
        f'{JUPYTER_SERVER_URL}/api/contents/{notebook_path}',
        headers={'Authorization': f'token {JUPYTER_TOKEN}'} if JUPYTER_TOKEN else {},
        json={
            'type': 'notebook',
            'format': 'json',
            'content': nb
        }
    )

    if save_response.status_code not in [200, 201]:
        # Fall back to file-based write
        nbformat.write(nb, notebook_path)

    return f"Inserted code cell at index {cell_idx} in {notebook_path}"

def modify_cell(
    notebook_path: Annotated[str, "Notebook filename"],
    cell_index: Annotated[int, "Index of cell to modify"],
    new_code: Annotated[str, "New code (empty string to delete cell)"]
) -> str:
    """
    Modify or delete an existing code cell in a notebook.

    Updates the source code of the cell at the specified index. If an empty string
    is provided as new_code, the cell is deleted instead. When modifying a cell,
    its outputs and execution count are cleared. The notebook is saved either via
    the Jupyter Server API (to maintain scroll position) or directly to the
    filesystem if the API save fails.

    Args:
        notebook_path: Path to the target notebook file. Leading slashes are
                      automatically stripped.
        cell_index: Zero-based index of the cell to modify or delete.
        new_code: The new Python source code for the cell. Pass an empty string ("")
                 to delete the cell instead of modifying it.

    Returns:
        A confirmation message indicating success, or an error message if the cell
        index is out of range or the cell is not a code cell.
    """

    notebook_path = notebook_path.strip("/")

    nb = nbformat.read(notebook_path, as_version=4)

    if cell_index < 0 or cell_index >= len(nb.cells):
        return f"Error: Cell index {cell_index} out of range (0-{len(nb.cells)-1})"

    # Delete cell if new_code is empty
    if new_code == "":
        removed_cell = nb.cells.pop(cell_index)
        result_msg = f"Deleted cell at index {cell_index} in {notebook_path}"
    else:
        # Modify cell
        cell = nb.cells[cell_index]
        # cell.metadata['jupyter'] = {'source_hidden': True}
        if cell.cell_type != 'code':
            return f"Error: Cell {cell_index} is not a code cell"

        cell.source = new_code
        cell.outputs = []  # Clear outputs when modifying
        cell.execution_count = None  # Reset execution count
        result_msg = f"Modified cell at index {cell_index} in {notebook_path}"

    # Save via API to maintain scroll position
    save_response = requests.put(
        f'{JUPYTER_SERVER_URL}/api/contents/{notebook_path}',
        headers={'Authorization': f'token {JUPYTER_TOKEN}'} if JUPYTER_TOKEN else {},
        json={
            'type': 'notebook',
            'format': 'json',
            'content': nb
        }
    )

    if save_response.status_code not in [200, 201]:
        # Fall back to file-based write
        nbformat.write(nb, notebook_path)

    return result_msg

def execute_cell(
    notebook_path: Annotated[str, "Notebook filename"],
    cell_index: Annotated[int, "Index of cell to execute"] = -1
) -> str:
    """
    Execute a code cell in the notebook's kernel and update its outputs.

    Automatically starts a kernel for the notebook if one isn't already running,
    then connects to it, executes the specified cell, collects all outputs (stdout,
    stderr, display data, errors), and updates the cell's execution count and outputs
    in the notebook file. The notebook is saved either via the Jupyter Server API
    (to maintain scroll position) or directly to the filesystem if the API save fails.

    Args:
        notebook_path: Path to the target notebook file. Leading slashes are
                      automatically stripped.
        cell_index: Zero-based index of the cell to execute. Defaults to -1 (last cell).

    Returns:
        A message containing the execution count and a summary of all outputs,
        including stream outputs (stdout/stderr), execution results, display data,
        and error tracebacks. Returns "(no output)" if the cell produces no output.

    Note:
        The kernel is automatically started via the Jupyter Server API if not already
        running. The kernel client is cached for reuse across multiple executions of
        the same notebook.
    """

    notebook_path = notebook_path.strip("/")

    nb = nbformat.read(notebook_path, as_version=4)

    cell = nb.cells[cell_index]

    if cell.cell_type != 'code':
        return f"Cell {cell_index} is not a code cell"

    # Connect to kernel (start one if it doesn't exist)
    if notebook_path not in kernel_clients:
        kernel_id = start_notebook_kernel(notebook_path)
        connection_file = find_connection_file(kernel_id)
        client = BlockingKernelClient()
        client.load_connection_file(connection_file)
        client.start_channels()
        kernel_clients[notebook_path] = client
    
    client = kernel_clients[notebook_path]
    msg_id = client.execute(cell.source)

    # Collect outputs and execution count
    outputs = []
    execution_count = None
    output_texts = []

    # Total-time budget for the whole cell. Poll iopub in short slices so we
    # can both honour the budget and avoid bailing out prematurely just because
    # a long-running cell went quiet between messages.
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

        if msg['parent_header'].get('msg_id') != msg_id:
            continue

        msg_type = msg['header']['msg_type']
        content = msg['content']

        if msg_type == 'execute_input':
            execution_count = content['execution_count']
        elif msg_type == 'stream':
            output = nbformat.v4.new_output('stream',
                name=content['name'], text=content['text'])
            outputs.append(output)
            output_texts.append(f"[{content['name']}] {content['text']}")
        elif msg_type == 'execute_result':
            output = nbformat.v4.new_output('execute_result',
                data=content['data'], execution_count=content['execution_count'])
            outputs.append(output)
            output_texts.append(content['data'].get('text/plain', str(content['data'])))
        elif msg_type == 'display_data':
            output = nbformat.v4.new_output('display_data', data=content['data'])
            outputs.append(output)
            output_texts.append(f"[display] {content['data'].get('text/plain', 'Rich content')}")
        elif msg_type == 'error':
            output = nbformat.v4.new_output('error',
                ename=content['ename'], evalue=content['evalue'],
                traceback=content['traceback'])
            outputs.append(output)
            error_msg = f"ERROR: {content['ename']}: {content['evalue']}\n" + '\n'.join(content['traceback'])
            output_texts.append(error_msg)
        elif msg_type == 'status' and content['execution_state'] == 'idle':
            break

    if timed_out:
        output_texts.append(
            f"[deepagent-lab] Cell exceeded EXECUTE_TIMEOUT={EXECUTE_TIMEOUT}s; "
            "output above may be incomplete. Set DEEPAGENT_EXECUTE_TIMEOUT to raise the budget."
        )
    
    # Update cell in notebook
    cell.execution_count = execution_count
    cell.outputs = outputs

    # Save via API to maintain scroll position
    save_response = requests.put(
        f'{JUPYTER_SERVER_URL}/api/contents/{notebook_path}',
        headers={'Authorization': f'token {JUPYTER_TOKEN}'} if JUPYTER_TOKEN else {},
        json={
            'type': 'notebook',
            'format': 'json',
            'content': nb
        }
    )

    if save_response.status_code not in [200, 201]:
        # Fall back to file-based write
        nbformat.write(nb, notebook_path)

    output_summary = '\n'.join(output_texts) if output_texts else "(no output)"
    return f"Executed cell [{execution_count}] in {notebook_path}:\n{output_summary}"


# === Agent Configuration ===

# Build the deep agent
system_prompt = """You're a JupyterLab assistant. Use the provided tools to manipulate and execute code cells in the specified notebook files as per user instructions.

# Guidelines:
- When asked a question, use the tool `write_todos` to plan your approach.
- If necessary, write code to answer user questions.
- Always write the requested code into a Jupyter notebook using the tools described below.
- You may choose to create a temporary Jupyter notebook file for intermediate steps.
- MOST IMPORTANTLY, ALWAYS execute the code cells right after inserting them and then modify to ensure they work correctly.

# Tools for writing Jupyter Notebooks:
- `get_notebook_state(notebook_path: str) -> str`: Gets the current state of a notebook, including total cells, executed/unexecuted cells, and the recommended next insertion index.
- `create_notebook(notebook_path: str) -> str`: Creates a new empty Jupyter notebook file at the specified path. Returns a confirmation message.
- `insert_code_cell(code: str, notebook_path: str, cell_idx: int = -1) -> str`: Inserts a new code cell with the given code into the specified notebook at `cell_idx` (default -1 appends at the end). Returns the index of the inserted cell.
- `modify_cell(notebook_path: str, cell_index: int, new_code: str) -> str`: Modifies the code of the cell at the specified index in the notebook. If `new_code` is an empty string, deletes the cell. Returns a confirmation message.
- `execute_cell(notebook_path: str, cell_index: int) -> str`: Executes the code cell at the specified index in the notebook and updates its outputs. Returns the execution result or error message. Automatically starts a kernel if needed.

# Examples:
User: Please create a new notebook "example.ipynb" and add a code cell that prints "Hello, World!", then execute it.
Assistant:
1. create_notebook("example.ipynb")
2. insert_code_cell('print("Hello, World!")', "example.ipynb")
3. execute_cell("example.ipynb", 0)

# Code execution:
- Always first create a new notebook if it doesn't exist.
- Insert code cells as needed and immediately execute them to verify correctness.
- Modify existing cells if corrections are needed to avoid errors.
- Ask the user for clarification if instructions are ambiguous or if you need help.
- NEVER run risky or harmful code without explicit user consent.
- ALWAYS execute code cells right after inserting or modifying them to ensure they work as intended.
- NEVER write a code cell and leave it unexecuted.
"""

# Build the chat model so MODEL_TEMPERATURE is actually applied.
chat_model = init_chat_model(MODEL_NAME, temperature=MODEL_TEMPERATURE)

# Build via the shared demo factory (owns the FilesystemBackend + checkpointer
# boilerplate). Lab supplies its nbformat notebook tools + system prompt.
agent = _build_default_agent(
    workspace=str(WORKSPACE),
    model=chat_model,
    name="Default Agent",
    system_prompt=system_prompt,
    tools=[get_notebook_state, create_notebook, insert_code_cell, modify_cell, execute_cell],
    virtual_mode=config.VIRTUAL_MODE,
)

# Log configuration if in debug mode
if config.DEBUG:
    print(f"Agent Configuration:")
    print(f"  Workspace: {WORKSPACE}")
    print(f"  Model: {MODEL_NAME}")
    print(f"  Virtual Mode: {config.VIRTUAL_MODE}")
    print(f"  Jupyter Server: {JUPYTER_SERVER_URL}")