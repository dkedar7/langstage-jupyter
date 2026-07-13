"""
Default agent configuration for langstage-jupyter.

This agent is used when no custom agent is specified. It provides basic
notebook manipulation capabilities with filesystem access.

The notebook tools themselves live in :mod:`langstage_jupyter.notebook_tools` so
they can be imported and tested without building a chat model. They are
re-exported here for backwards compatibility.
"""
from dotenv import find_dotenv, load_dotenv

# Resolve .env from the user's working (launch) directory, not the installed
# package location — a bare load_dotenv() searches from site-packages and never
# finds the user's project .env. (gh #32)
load_dotenv(find_dotenv(usecwd=True))

from langchain.chat_models import init_chat_model

from langstage_core import workspace_root
from langstage_core.demo import create_default_agent as _build_default_agent

from langstage_jupyter import config
from langstage_jupyter.notebook_tools import (  # noqa: F401 - re-exported
    EXECUTE_TIMEOUT,
    NOTEBOOK_TOOLS,
    NotebookNotFound,
    create_notebook,
    delete_cell,
    execute_cell,
    get_notebook_kernel_id,
    get_notebook_state,
    insert_code_cell,
    kernel_clients,
    modify_cell,
    read_cell,
    start_notebook_kernel,
)

# === Configuration ===

# Workspace root comes from the shared source of truth (ADR 0005). The agent
# wrapper calls core.apply_workspace() with the resolved root (pinned config root,
# else JupyterLab's live launch dir) BEFORE this module builds/rebuilds the agent,
# so workspace_root() here is authoritative — and honors canonical
# LANGSTAGE_WORKSPACE_ROOT / legacy / langstage.toml with the correct precedence.
WORKSPACE = workspace_root()

MODEL_NAME = config.MODEL_NAME
MODEL_TEMPERATURE = config.MODEL_TEMPERATURE

# === Agent Configuration ===

system_prompt = """You're a JupyterLab assistant. Use the provided tools to manipulate and execute code cells in the specified notebook files as per user instructions.

# Guidelines:
- When asked a question, use the tool `write_todos` to plan your approach.
- If necessary, write code to answer user questions.
- Always write the requested code into a Jupyter notebook using the tools described below.
- You may choose to create a temporary Jupyter notebook file for intermediate steps.
- MOST IMPORTANTLY, ALWAYS execute the code cells right after inserting them and then modify to ensure they work correctly.

# Tools for writing Jupyter Notebooks:
- `get_notebook_state(notebook_path: str) -> str`: Current state of a notebook — cell count, which cells have run, the next insertion index, and a one-line preview of every cell.
- `read_cell(notebook_path: str, cell_index: int) -> str`: The full source of one cell. Read a cell before modifying it instead of guessing its contents.
- `create_notebook(notebook_path: str, overwrite: bool = False) -> str`: Creates a new EMPTY notebook. If the notebook already exists it is left untouched and nothing is overwritten — only pass overwrite=True when you deliberately want to destroy its existing cells.
- `insert_code_cell(code: str, notebook_path: str, cell_index: int = -1) -> str`: Inserts a new code cell at `cell_index` (default -1 appends at the end).
- `modify_cell(notebook_path: str, cell_index: int, new_code: str) -> str`: Replaces the code of the cell at `cell_index` (clearing its old outputs). It does NOT delete — use `delete_cell` for that.
- `delete_cell(notebook_path: str, cell_index: int) -> str`: Deletes the cell at `cell_index`.
- `execute_cell(notebook_path: str, cell_index: int = -1) -> str`: Executes the code cell at `cell_index` and stores its outputs. Returns the outputs or the error. Starts the notebook's kernel if needed.

All of these return a string starting with "Error: ..." when something is wrong (missing notebook, index out of range) — read it and correct your next call rather than repeating the same one.

# Examples:
User: Please create a new notebook "example.ipynb" and add a code cell that prints "Hello, World!", then execute it.
Assistant:
1. create_notebook("example.ipynb")
2. insert_code_cell('print("Hello, World!")', "example.ipynb")
3. execute_cell("example.ipynb", 0)

# Code execution:
- Create the notebook first if it does not exist. If it already exists, just use it — do NOT overwrite it, as that destroys the user's work.
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
    name="default-agent",
    system_prompt=system_prompt,
    tools=list(NOTEBOOK_TOOLS),
    virtual_mode=config.VIRTUAL_MODE,
)

# Log configuration if in debug mode
if config.DEBUG:
    print("Agent Configuration:")
    print(f"  Workspace: {WORKSPACE}")
    print(f"  Model: {MODEL_NAME}")
    print(f"  Virtual Mode: {config.VIRTUAL_MODE}")
    print(f"  Jupyter Server: {config.JUPYTER_SERVER_URL}")
