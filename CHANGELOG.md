# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-06-10

### Added

- **`deepagent-lab -a/--agent <spec>`** — pick the agent straight from the launcher (`-a x`, `--agent x`, `--agent=x`). The launcher extracts the flag before passing the remaining args to `jupyter lab` and exports `DEEPAGENT_AGENT_SPEC` for the sidebar extension.
- **`deepagent-lab --demo`** — launch with the shared keyless echo agent (`langgraph_stream_parser.demo.stub:graph`): the whole extension runs with no API key.
- README: *One agent, every surface* family table + launcher flag docs.

### Changed

- `langgraph-stream-parser` pinned `>=0.2.2,<0.3` (the release that ships the demo stub).

## [0.2.1] - 2026-06-04

### Fixed
- **Chat sidebar now honors JupyterLab's light/dark theme.** It previously hardcoded a light palette and stayed light in dark mode (a jarring light island). The `--da-*` design tokens now resolve from JupyterLab's `--jp-*` theme variables (with light fallbacks).

### Changed
- **Design-coherence pass on the sidebar** to feel native to JupyterLab: flattened the styling (removed gradients, drop-shadows, hover transforms, and the infinite status-dot pulse), unified the rounding scale (anchored on `--jp-border-radius`), aligned fonts to `--jp-ui-font-size*` / `--jp-ui-font-family` and raised the smallest size from 10px to 11px, and themed the approval/interrupt alert off `--jp-warn-*` instead of hardcoded amber.

### Added
- Visual-regression gate (Galata) for the chat sidebar in light + dark, guarding the styling against regressions. Dev-only — not part of the published package.

## [0.2.0] - 2026-06-02

### Changed
- **Shared streaming runtime.** Streaming now routes through `langgraph-stream-parser` (typed events + `stream_graph_updates`); the in-tree parser was removed. New dependency: `langgraph-stream-parser>=0.2,<0.3`.
- **Shared config layer.** `LabConfig` subclasses the shared `HostConfig` and resolves through `defaults < deepagents.toml < DEEPAGENT_* env < overrides`, adding **`deepagents.toml`** support. `DEEPAGENT_AGENT_SPEC` is the canonical agent selector.
- Default model bumped to `claude-sonnet-4-6`.

### Fixed
- **`insert_code_cell` arg mismatch.** The system prompt documented a `position` argument, but the real parameter is `cell_idx` — models calling the documented name hit a `TypeError`.
- **`execute_cell` silent truncation.** The iopub poll used a bare `except:` with a 5s per-message timeout, so long-running cells returned partial output as if they had finished. Replaced with a total-time budget (`DEEPAGENT_EXECUTE_TIMEOUT`, default 300s) that polls until the kernel reports idle and surfaces timeouts explicitly in the returned text.
- **`MODEL_TEMPERATURE` ignored.** It was read from config but never passed to the model. The agent now builds a configured model via `init_chat_model` before handing it to `create_deep_agent`.

### Added
- Galata (Playwright) UI smoke test for the chat sidebar, plus a CI workflow. Runs against a model-free stub agent, so it needs no API key. Dev-only — not part of the published package.

## [0.1.4] - 2025-12-26

### Added
- **Zero-Configuration Launcher**: New `deepagent-lab` command that automatically configures Jupyter server settings
  - Auto-detects available ports using socket programming
  - Generates secure authentication tokens with `secrets.token_urlsafe(32)`
  - Sets `DEEPAGENT_JUPYTER_SERVER_URL` and `DEEPAGENT_JUPYTER_TOKEN` environment variables automatically
  - Supports all `jupyter lab` arguments (e.g., `--no-browser`, `--port`)
  - See [JUPYTER_AUTO_CONFIG.md](JUPYTER_AUTO_CONFIG.md) for details

- **Dynamic Agent Name Display**: Chat interface now displays custom agent names
  - Reads the `name` attribute from agent objects
  - Updates dynamically when agents are switched via `DEEPAGENT_AGENT_SPEC`
  - Falls back to "Deep Agents" if no name is set

- **Custom Logo Integration**: Extension now uses custom DeepAgent Lab logo
  - Theme-aware SVG icon in sidebar and command palette
  - Centralized icon definitions in `src/icons.ts`
  - Professional branding throughout the interface

### Changed
- **Improved README**: Completely restructured documentation
  - Launcher command featured as recommended approach
  - Manual configuration shown as alternative method
  - Dedicated "Using Custom Agents" section with clear examples
  - Simplified Quick Start instructions
  - Enhanced environment variables reference table

- **Icon-Only Sidebar Tab**: Cleaner sidebar appearance
  - Removed label text from sidebar tab (icon only)
  - Moved extension to bottom of sidebar for better organization
  - Agent name still displayed in chat window header
  - Tooltip shows "Deep Agents" on hover

### Technical Details
- Added `[project.scripts]` entry point in `pyproject.toml` for launcher command
- Created `deepagent_lab/launcher.py` with port detection and token generation
- Modified health check endpoint to return agent name when available
- Updated chat widget to display dynamic agent names
- Removed obsolete auto-configuration code from extension initialization

## [0.1.3] - 2025-12-14

### Added
- **LangGraph Utilities Module**: New `langgraph_utils.py` providing helper functions for LangGraph integration
  - Utility functions for agent state management and graph operations
  - Enhanced agent execution framework with better LangGraph support

### Changed
- **Improved Chat Message Styling**: Enhanced message content styling for better readability
  - Clearer visual hierarchy and structure
  - More responsive and polished UI design
  - Better spacing and typography for message content

- **Refactored Interrupt Handling**: Improved interrupt mechanism for human-in-the-loop interactions
  - More reliable interrupt processing
  - Better clarity in interrupt flow
  - Enhanced responsiveness during agent execution

- **Enhanced Notebook Cell Operations**: Updated to use Jupyter Server API for saving changes
  - Direct integration with Jupyter Server API for cell modifications
  - More reliable cell save operations
  - Better notebook state management

- **Agent Execution Refactoring**: Streamlined agent execution logic in `agent_wrapper.py`
  - Cleaner execution flow leveraging LangGraph utilities
  - Improved error handling and state management
  - Better integration with LangGraph graph structures

### Fixed
- Improved stability and reliability of notebook cell operations

## [0.1.2] - 2025-11-29

### Added
- **Centralized Configuration System**: New `config.py` module with `get_config()` function for hierarchical environment variable support
  - All configuration now uses `DEEPAGENT_` prefix (not `DEEPAGENT_LAB_`)
  - Full compatibility with [deepagent-dash](https://github.com/dkedar7/deepagent-dash)
  - Agents can be shared between deepagent-lab and deepagent-dash seamlessly

- **File Path Agent Loading**: Enhanced agent loading to support both module paths and file paths
  - `DEEPAGENT_AGENT_SPEC` environment variable in format `"module_or_file:variable"`
  - Support for relative paths (`./my_agent.py:agent`)
  - Support for absolute paths (`/path/to/agent.py:graph`)
  - Automatic detection of file vs module paths

- **Dynamic Workspace Configuration**: Workspace root now configurable via `DEEPAGENT_WORKSPACE_ROOT`
  - Automatic workspace discovery for agents
  - Environment variable set by extension for agent access
  - Dynamic workspace path resolution

### Changed
- **Standardized Environment Variables**: All variables now use `DEEPAGENT_` prefix for cross-library compatibility
  - `DEEPAGENT_AGENT_SPEC` replaces previous agent configuration
  - `DEEPAGENT_JUPYTER_SERVER_URL` for Jupyter server connection
  - `DEEPAGENT_JUPYTER_TOKEN` for authentication
  - `DEEPAGENT_MODEL_NAME` and `DEEPAGENT_MODEL_TEMPERATURE` for model configuration
  - `DEEPAGENT_VIRTUAL_MODE` for FilesystemBackend safety
  - `DEEPAGENT_WORKSPACE_ROOT` for dynamic workspace paths

- **Enhanced Security**: Updated default Jupyter token from `"12345"` to cryptographically secure random value
  - `.env.example` includes command to generate secure tokens: `python3 -c "import secrets; print(secrets.token_hex(16))"`
  - Default token: `8e2121e58cd3f9e13fc05fc020955c6e`

- **Streamlined Documentation**: Updated README.md with clearer, more concise instructions
  - Emphasized critical Jupyter server configuration requirements
  - Added environment variables reference table
  - Highlighted agent portability between deepagent-lab and deepagent-dash
  - Removed verbose sections to focus on essential information

- **Agent Initialization**: Updated `agent_wrapper.py` with improved loading mechanisms
  - Smart detection of file paths vs module paths
  - Support for `importlib.util` for file-based loading
  - Workspace root environment variable propagation

### Removed
- **Unused Environment Variables**: Removed `MODEL_MAX_TOKENS` and `LOG_LEVEL` (were defined but never used)

### Fixed
- **Jupyter Configuration**: Corrected Quick Start documentation to use hardcoded values matching `.env.example`
  - Fixed incorrect reference to non-existent `DEEPAGENT_JUPYTER_PORT` variable
  - Updated jupyter lab command with correct port and token values

## [0.1.1] - 2025-11-19

### Added
- **Stop Execution Button**: Added ability to cancel ongoing agent execution with a red stop button that replaces the send button during processing
  - Backend cancellation endpoint (`/cancel`) with thread-safe execution tracking
  - Graceful cancellation between streaming chunks
  - User feedback when execution is cancelled

- **Multi-line Input Support**: Input box now supports multiple lines for longer messages
  - Enter key sends message
  - Shift+Enter creates new line
  - Manual vertical resizing with drag handle
  - Auto-constrained between 40px and 200px height

- **Non-blocking Execution**: Agent operations now run in thread pool to keep Jupyter responsive
  - Notebooks remain interactive while agent is working
  - No UI freezing during long-running operations
  - Thread pool with 4 max workers for concurrent operations

### Changed
- Professional UI redesign with light color palette
  - Blue gradient send button with hover effects
  - Red gradient stop button with professional styling
  - Refined shadows, borders, and spacing throughout
  - System messages now left-aligned with subtle gray styling

- Improved markdown rendering with compact spacing
  - Reduced paragraph padding to 0.1em vertical
  - Tighter line height for efficient reading
  - Better typography consistency

### Fixed
- Fixed raw tool call dictionaries appearing in chat output
- Fixed todo list parsing to handle Python-style single quotes using `ast.literal_eval()`
- Fixed todo list display issues after content filtering

## [0.1.0] - 2025-11-17

### Added
- Initial release
- JupyterLab extension with chat interface
- DeepAgents integration
- Real-time streaming responses
- Tool call visualization
- Todo list tracking
- Human-in-the-loop interrupts
- Context awareness (current directory and focused widget)
