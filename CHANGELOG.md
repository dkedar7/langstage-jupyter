# Changelog

## 0.6.12 - 2026-07-12

### Fixed
- **`--verify` on the bundled default agent with no `ANTHROPIC_API_KEY` now names the
  missing variable instead of dumping a raw provider `TypeError` (gh #66).** The README
  advertises `--verify` as the preflight that "catches a bad API key"; but the default
  agent's model is built lazily, so a missing key didn't fail until the first API call —
  and `core.verify()` (provider-agnostic) surfaced it as `TypeError: Could not resolve
  authentication method...`, which never mentions the key. The launcher already knows this
  branch is the default agent, so `--verify` now does the same cheap credential preflight
  `/health` does (gh #60) *before* building it, short-circuiting with `ANTHROPIC_API_KEY is
  not set — the default agent's first turn would fail. Set it and re-run.` The two
  preflights (`--verify` and the sidebar/`/health`) now say the same thing about the same
  failure. A custom/BYO agent (`-a`/spec) is unchanged — its credentials stay the
  operator's concern and it keeps the full one-real-turn check. Shares the provider-key
  lookup with `/health` via a new `handlers._missing_provider_key(model_name)` helper.

## 0.6.11 - 2026-07-09

### Fixed
- **The headline `langstage-jupyter` launcher died immediately when run as root (gh #64).**
  jupyter_server refuses to boot as root without `--allow-root`, so the primary zero-config
  command exited `1` (after the extension loaded) on the `jupyter/*` Docker images, Binder,
  CI runners, K8s notebook pods, and devcontainers — the exact root environments
  `--serve-check` was hardened for (gh #58). The launch path now mirrors that treatment:
  when `os.geteuid() == 0` and the user hasn't already passed it, it injects `--allow-root`
  (same rationale as #58 — a token-gated, localhost server). A non-root laptop is unchanged,
  and an explicit user `--allow-root` isn't duplicated.

## 0.6.10 - 2026-07-08

### Fixed
- **The launcher now propagates JupyterLab's exit code (gh #62).** `langstage-jupyter` ran
  `jupyter lab` via `subprocess.run(...)` but discarded the return code, so a startup
  failure (port in use, a fatal config error, the root guard) still exited **0** — masking
  the failure from `set -e`, CI steps, systemd, healthchecks, and `langstage-jupyter && …`.
  It now exits with the child's return code.

## 0.6.9 - 2026-07-07

### Fixed
- **The sidebar status no longer shows 🟢 "ready" for an agent that can't actually run a
  turn (gh #60).** Readiness was `agent is not None`, but the bundled default agent's model
  is built lazily — it constructs fine with no `ANTHROPIC_API_KEY` and only fails at the
  first API call — so a common first-run (no key) lit green, then the first message failed
  with a raw provider auth error. Readiness now reflects runnability: the `/health` endpoint
  returns a `ready` flag gated on the agent being a runnable graph **and** (for the default
  agent) its provider key being present, and the sidebar shows a distinct 🟠 `needs_setup`
  state with an actionable tooltip (e.g. "ANTHROPIC_API_KEY is not set — the first turn will
  fail") instead of green. A custom/BYO agent's credentials remain the operator's concern.

### Fixed
- **`--serve-check` now works when running as root — i.e. in CI and Docker, the
  environments it targets (gh #58).** `serve_check()` spawned its `jupyter_server`
  without `--allow-root`, so the server hit Jupyter's root guard and exited before it
  could serve; the spawned server now passes `--ServerApp.allow_root=True` (safe: an
  ephemeral, token-gated, localhost-only smoke server). And when the server exits before
  it is ready, the verdict now includes the server's own last output lines (the real
  cause — a root guard, a port clash, a config error) instead of a bare exit code.

## 0.6.7 - 2026-07-05

### Added
- **`langstage-jupyter --serve-check` — a headless HTTP smoke test of the deployed
  extension (gh #56).** The HTTP counterpart of `--verify`: where `--verify` proves the
  *agent object* completes a turn, `--serve-check` boots the server extension headlessly,
  polls `/langstage-jupyter/health` until the agent is loaded, POSTs one turn to
  `/langstage-jupyter/chat`, and asserts the SSE stream yields a non-empty chunk and
  completes — catching route/registration/handler regressions (e.g. the #53 empty-body
  500) that `--verify` structurally can't. Exits `0`/`1`; defaults to the keyless demo
  agent (CI-safe) and honors `-a` to smoke-test a real agent. `--smoke` is an alias.

### Docs
- Documented the two preflights (`--verify`, `--serve-check`) and the extension's served
  routes (`/<base_url>langstage-jupyter/{health,chat,resume,reload,cancel}`) in the README.

## 0.6.6 - 2026-07-04

### Changed
- **The chat sidebar is now branded "LangStage", not "Deep Agents" (dogfood).** The
  JupyterLab left-rail tab tooltip, launcher entry, and default agent-name fallback all
  said "Deep Agents" (the pre-rename name); they now read "LangStage". Command IDs and
  CSS classes (`deepagents:open-chat`, `.deepagents-*`) are unchanged, so nothing that
  targets them breaks.

### Docs
- Refreshed the README header to a `langstage-jupyter` SVG banner (was a remote
  `cover.png` labelled "DeepAgent Lab", the old name).

### Fixed
- **Empty POST body to `/chat`, `/resume`, `/cancel` returned HTTP 500 instead of
  400 (gh #53).** Jupyter Server's `get_json_body()` returns `None` for an empty
  body (it only raises on *invalid* JSON), so the handlers hit `None.get(...)` →
  unhandled `AttributeError` → 500. All three now guard `data is None` and raise
  `HTTPError(400, "Request body must be a JSON object")`, matching the adjacent
  malformed-input handling. Reachable from a frontend race, a proxy that drops the
  body, or a stray client.

## 0.6.4 - 2026-07-03

### Changed
- **Workspace root now flows through the shared `core.apply_workspace()` /
  `core.workspace_root()` source of truth (ADR 0005).** The extension used to apply
  the workspace via a bespoke dance — a mutable `config.WORKSPACE_ROOT` global,
  manual `os.environ` writes, and `set_root_dir()` mutating that global to re-root —
  which drifted into #45 (pinned root discarded) and #36 (dead re-root). Now
  `agent_wrapper` calls `apply_workspace()` (pinned root at init, else JupyterLab's
  live launch dir on each message) and the default agent reads `workspace_root()` —
  one source the env, the backend, and the rebuilt agent all agree on. Behavior is
  preserved (pinned still wins over the launch dir; re-root still happens on an
  actual change); the published env value is now the *resolved* absolute root.
  Requires `langstage-core>=1.0.7`.


### Added
- **`langstage-jupyter --verify`: preflight the agent with one real turn (ADR 0004).**
  The extension previously had no real readiness check — the `/health` endpoint only
  reports whether the agent object is non-None. `--verify` resolves the same spec the
  extension would run, loads it, and runs ONE real turn through the shared
  `langstage-core` primitive `core.verify()`, exiting **0** if it completed cleanly /
  **non-zero** otherwise (use `--demo` for a keyless check). Catches a missing key /
  broken tool / bad graph before you launch. Requires `langstage-core>=1.0.6`.

## 0.6.2 - 2026-07-03

### Fixed
- **The 0.6.1 wheel bundled a stale labextension reporting v0.6.0 (gh #48).** 0.6.1
  was a Python-only fix, and the build hook skips the labextension rebuild when a
  built copy already exists — so the published wheel shipped the previous release's
  JS bundle (correct behavior, wrong version label). Rebuilt cleanly so the bundled
  labextension version matches the package (verified `0.6.2` in the wheel before
  publish). No functional change to the extension.

## 0.6.1 - 2026-07-02

### Fixed
- **An explicit workspace root was silently discarded (gh #45).** `set_root_dir`
  runs on every chat message and re-rooted the agent (and overwrote
  `LANGSTAGE_WORKSPACE_ROOT`) to JupyterLab's launch dir — clobbering a workspace
  the operator had pinned via `LANGSTAGE_WORKSPACE_ROOT` / legacy
  `DEEPAGENT_WORKSPACE_ROOT` or `workspace.root` in `langstage.toml` (advertised ≠
  honored). A pinned root is now honored: the auto-follow only applies when no
  workspace is pinned. The pinned value is captured at wrapper init (before
  `set_root_dir` can mutate it).

## 0.6.0 - 2026-07-02

### Changed
- **AG-UI is now the chat sidebar's only streaming path (ADR 0003).** The
  built-in event-parser path (`stream_graph_updates`) is gone; `AgentWrapper.execute()`
  always streams through `langstage-core`'s in-process AG-UI adapter, yielding the
  **same** chunk shapes the React frontend already consumes — the UI is unchanged.
  Removed the `LANGSTAGE_JUPYTER_AGUI` opt-in env and the `config.AGUI` toggle
  (they gated a path that no longer exists).
- **Repointed to `langstage-core` 1.0** (the rename of `langgraph-stream-parser`;
  ADR 0003). The AG-UI runtime (`ag-ui-langgraph[fastapi]` + uvicorn, via core's
  `[agui]` extra) moved into **base dependencies**: since AG-UI is the only path,
  a bare `pip install langstage-jupyter` must be able to run a turn. The `[agui]`
  extra is now a redundant no-op alias.

### Fixed
- **Chat replies rendered fragmented under AG-UI.** The frontend accumulated each
  streamed chunk as a separate "intermediate" and overwrote content with the last
  chunk — built for the old path's cumulative per-message chunks. AG-UI streams
  token *deltas*, so a normal reply showed as many grey one-token fragments with
  only the last token as the message body. The stream handler now accumulates
  consecutive same-node deltas into one message (a node/tool break starts a new
  intermediate), so a reply renders as one clean block again — matching the
  pre-AG-UI rendering. (Surfaced by the Galata visual tests.)

### Removed
- The `stream_graph_updates`/`prepare_agent_input` imports and the legacy turn
  path in `execute()`; the `config.AGUI` flag and its env. Inputs are now validated
  before the agent is built, so a bad call fails fast.

## 0.5.9 - 2026-06-27

### Fixed
- **`--show-config` advertised more keys the launcher doesn't honor.** Completing
  the #30 fix: `title` is inherited from the web-app `HostConfig` but read
  nowhere in this stage, and `jupyter_token` / `jupyter_server_url` are
  auto-generated/-detected at startup and overridden by the launcher (a
  user-set `LANGSTAGE_JUPYTER_TOKEN` is silently discarded — pin via the
  standard `JUPYTER_TOKEN`). All three are now dropped from the launcher's
  `--show-config` via `describe(omit_keys=…)`, so it only advertises keys this
  stage actually honors. (Found by the dogfood routine, gh #34.)

## 0.5.8 - 2026-06-26

### Fixed
- **`.env` files were silently ignored at runtime.** `agent.py` and
  `agent_wrapper.py` called a bare `load_dotenv()`, which searches upward from
  the calling module — inside `site-packages` once installed — so a user's
  project `.env` (the documented "create a `.env` file" path, and the whole
  point of `.env.example`) was never found, and every setting in it silently
  fell back to defaults. Both now resolve via `load_dotenv(find_dotenv(usecwd=True))`,
  anchoring `.env` to the launch directory (matching how `langstage.toml` is
  already discovered). Exported shell env vars were unaffected. (Found by the
  dogfood routine, gh #32.)

## 0.5.7 - 2026-06-25

### Fixed
- **`--show-config` advertised `LANGSTAGE_PORT` / `LANGSTAGE_HOST`, which the
  launcher ignores.** Those keys are inherited from the shared `HostConfig`
  (real for the web-app stage), but JupyterLab's port is always the
  auto-detected port or `--port`, and the host is always `localhost`. So
  `--show-config` reported a value and a "source" env var with zero effect —
  teaching a wrong mental model ("I set `LANGSTAGE_PORT` but it didn't apply").
  The two inert rows are now dropped from `--show-config` via core's new
  `describe(omit_keys=…)` (requires `langgraph-stream-parser>=0.6.11`). (Found
  by the dogfood routine, gh #30.)

## 0.5.6 - 2026-06-22

### Fixed
- **JupyterLab → agent workspace hand-off only set the *legacy* env name.**
  `AgentWrapper.set_root_dir()` published the live JupyterLab root as
  `DEEPAGENT_WORKSPACE_ROOT` only, but the README's own custom-agent example
  (and the documented env table) read the canonical `LANGSTAGE_WORKSPACE_ROOT`.
  A user following the docs verbatim got `'.'` instead of their notebook project
  directory. The agent's *read* path was renamed in 0.5.4, but this *write* path
  still published the deprecated name. It now sets both (canonical + legacy).
  (Found by the dogfood routine.)

## 0.5.5 - 2026-06-21

### Fixed
- **`langstage-jupyter --help`** passed straight through to `jupyter lab` and never
  documented the launcher's own flags (`--demo`, `-a/--agent`, `--show-config`,
  `--version`). It now prints a short launcher usage block (ASCII-only, so it can't
  mojibake on a cp1252 console) and points at `jupyter lab --help` for the rest.
- **`--show-config` ignored `-a/--agent` and `--demo`** (it short-circuited before
  parsing them, always reporting `agent_spec=None`). Agent flags are now parsed
  first, so `--show-config` reflects the agent the same invocation would launch.
- **The cell-timeout warning** told users to set the deprecated
  `DEEPAGENT_EXECUTE_TIMEOUT`; it now names the canonical `LANGSTAGE_EXECUTE_TIMEOUT`.

### Docs
- Regenerated `.env.example` with canonical `LANGSTAGE_*` names (it was 100%
  pre-rename `DEEPAGENT_*` and referenced the old `deepagent-lab`/`deepagent-dash`
  identity), aligned to the current default model.

## 0.5.4 - 2026-06-20

### Fixed
- **`langstage.toml` was silently ignored by the default agent (gh #-dogfood).**
  The module-level config constants were resolved with `use_toml=False`, so the
  agent ran on env+defaults while `--show-config` advertised `langstage.toml` as a
  live source. The constants now resolve with TOML on, matching `--show-config`.
- **Canonical `LANGSTAGE_EXECUTE_TIMEOUT` was ignored** (only the deprecated
  `DEEPAGENT_EXECUTE_TIMEOUT` worked). `EXECUTE_TIMEOUT` now comes from the
  resolved `LabConfig` (canonical env + legacy + TOML), and the `get_config()`
  helper checks the canonical `LANGSTAGE_*` name first.
- **Workspace-root env precedence was inverted** — `agent.py` read
  `DEEPAGENT_WORKSPACE_ROOT` directly *before* the resolved config, so the legacy
  name overrode the canonical `LANGSTAGE_WORKSPACE_ROOT`. It now defers to the
  resolved `config.WORKSPACE_ROOT` (canonical wins, legacy warns).
- **Launcher booted the wrong Jupyter (gh #-dogfood).** It shelled out to a bare
  `jupyter` from `PATH`; when another Jupyter preceded the venv on `PATH` (common
  on Windows), the chat extension silently never loaded. It now launches via this
  interpreter (`sys.executable -m jupyterlab`), guaranteeing the env that owns the
  labextension + server-config.
- **`langstage-jupyter --version`** reported JupyterLab's version (it was passed
  through to `jupyter lab`); it now prints this package's version and exits.

## 0.5.3 - 2026-06-18

### Fixed
- **First-run launcher failure on a clean install (gh #24).** `jupyterlab` was pinned
  only in `[build-system].requires`, so `pip install langstage-jupyter` left the user
  without a Lab UI and the headline `langstage-jupyter` launcher died on
  `Jupyter command 'jupyter-lab' not found`. Declared `jupyterlab>=4.0.0,<5` as a runtime
  dependency. The launcher's old `except FileNotFoundError` guard never fired (the
  `jupyter` dispatcher *is* present), so it now pre-checks
  `importlib.util.find_spec("jupyterlab")` and prints an actionable `pip install jupyterlab`
  hint before launching.
- **Default agent name 400'd OpenAI-compatible providers (gh #23).** The shipped default
  `name="Default Agent"` flows into the LLM message `name` field, which OpenAI-compatible
  providers (e.g. via OpenRouter) require to match `^[^\s<|\\/>]+$` — no spaces — so the
  default agent hard-`400`'d on the second turn. Renamed the default to `default-agent`
  and bumped the core pin to `langgraph-stream-parser>=0.6.3`, which slugifies any unsafe
  display name as an upstream backstop for custom agents.

### CI
- The `test` job now installs `jupyterlab` (declared runtime dep) and pins
  `langgraph-stream-parser>=0.6.3` to match pyproject.


## 0.5.2 - 2026-06-16

### Changed
- Modernized the `langgraph-stream-parser` pin `>=0.3,<0.5` -> `>=0.6,<0.7` (and the
  `[agui]` extra) — it was several majors behind the rest of the family. CI now installs
  the package from pyproject for that dependency instead of a hardcoded stale range.


## 0.5.1 - 2026-06-16

### Fixed
- Declare previously-undeclared runtime dependencies that `langstage_jupyter/agent.py`
  imports at module top level — `langchain`, `requests`, `nbformat`, `jupyter_client`,
  `tornado`. They were only present transitively (langchain via deepagents, etc.), so a
  fresh `pip install langstage-jupyter` could `ModuleNotFoundError` on those paths. Found
  by rolling the family's minimal-install CI guard.

### CI
- Added a `minimal-install` job (install with no extras + deep import smoke incl. agent.py).


All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-06-14

### Added

- Adopt AG-UI: widen the langgraph-stream-parser ceiling to `<0.5` and add an `[agui]` extra so this surface's agent can be served over AG-UI via `langstage-agui`. Additive; no runtime changes.

## [0.4.0] - 2026-06-12

**deepagent-lab is now `langstage-jupyter`** — the JupyterLab stage of the LangStage family ("every stage for your LangGraph agent").

### Changed

- Distribution `deepagent-lab` → **`langstage-jupyter`**; module `deepagent_lab` → **`langstage_jupyter`**; npm/labextension name `langstage-jupyter`. A deprecated alias package keeps `import deepagent_lab` working with a `DeprecationWarning`; the `deepagent-lab` launcher command remains as an alias of `langstage-jupyter`.
- Canonical config vocabulary via langgraph-stream-parser 0.3: `LANGSTAGE_*` env vars (`LANGSTAGE_AGENT_SPEC`, `LANGSTAGE_JUPYTER_SERVER_URL`, `LANGSTAGE_JUPYTER_TOKEN`, ...), project `langstage.toml`, global `~/.langstage/config.toml`. The full legacy `DEEPAGENT_*` / `deepagents.toml` vocabulary still resolves; the launcher exports both spellings so mixed old/new installs keep working.
- Parser pinned `>=0.3,<0.4`.

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
