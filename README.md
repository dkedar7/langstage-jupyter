<p align="center">
  <img src="https://storage.googleapis.com/deepagents/cover.png" alt="DeepAgent Lab" width=600>
</p>

<p align="center">
    <em>Bring LangChain agents into your JupyterLab workflow</em>
</p>

---

* **Source code**: [github.com/dkedar7/langstage-jupyter](https://github.com/dkedar7/langstage-jupyter/)
* **Installation**: `pip install -U langstage-jupyter`  *(renamed from `deepagent-lab` — the old name now just installs this one, and the `deepagent-lab` command still works)*

---

A JupyterLab extension to allow **your** LangChain agents access to JuputerLab notebooks and files, enabling natural language interactions with your data science projects **directly from JupyterLab**.

<p align="center">
  <img src="https://storage.googleapis.com/deepagents/screenshot1.png" alt="DeepAgent Lab Demo" width=800>
</p>

Watch the full demo video here: [https://www.youtube.com/watch?v=vGA2vzMSQzo](https://www.youtube.com/watch?v=vGA2vzMSQzo)

## Every stage for your LangGraph agent

langstage-jupyter is the JupyterLab stage of the **LangStage family**: write your agent once — any LangGraph `CompiledGraph` — and run it on every stage with the same spec string (`module:attr` or `path/to/file.py:attr`), the same `langstage.toml` config file, and the same `LANGSTAGE_*` environment variables.

| Stage | Package | Try it |
|---|---|---|
| Web app | [langstage](https://github.com/dkedar7/langstage) | `langstage run --agent my_agent.py:graph` |
| JupyterLab | langstage-jupyter | **you are here** |
| Terminal | [langstage-cli](https://github.com/dkedar7/langstage-cli) | `langstage-cli -a my_agent.py:graph` |
| VS Code | [langstage-vscode](https://github.com/dkedar7/langstage-vscode) | chat participant + stdio sidecar |
| Reference agent | [langstage-hermes](https://github.com/dkedar7/langstage-hermes) | `LANGSTAGE_AGENT_SPEC=langstage_hermes.agent:graph` on any stage |
| Shared core | [langstage-core](https://github.com/dkedar7/langstage-core) | typed events + config resolver + AG-UI bridge behind every stage |

### Serve over AG-UI

The chat sidebar already streams every turn through the in-process AG-UI adapter. Your agent — any LangGraph `CompiledGraph` — can also be served over the [AG-UI protocol](https://github.com/dkedar7/langstage-core) as a standalone HTTP endpoint:

```bash
pip install "langstage-core[agui]"
langstage-agui --agent my_agent.py:graph
```

📖 **Full documentation:** <https://dkedar7.github.io/langstage-docs/>

## Features

- **Chat Interface**: Sidebar for natural conversations with your agent
- **Notebook Manipulation**: Built-in tools for creating, editing, and executing Jupyter notebooks
- **Human-in-the-Loop**: Review and approve agent actions before execution
- **Context Awareness**: Automatically sends workspace and file context to your agent
- **Custom Agents**: Use your own langgraph-compatible agents seamlessly
- **Auto-Configuration**: Zero-config setup with automatic Jupyter server detection

## Installation

```bash
pip install langstage-jupyter
```

## Quick Start

### Recommended: Using the Launcher (Zero Configuration)

Instead of `jupyter lab`, use `langstage-jupyter` command for automatic setup.

The easiest way to get started is using the `langstage-jupyter` launcher command, which automatically configures everything for you:

```bash
# Set your API key (if using the default agent)
export ANTHROPIC_API_KEY=your-api-key-here

# Start JupyterLab with auto-configuration
langstage-jupyter
```

That's it! The launcher will:
- Auto-detect an available port (starting from 8888)
- Generate a secure authentication token
- Set the required environment variables
- Launch JupyterLab with the proper configuration

**Using custom arguments:**
```bash
# All jupyter lab arguments are supported
langstage-jupyter --no-browser
langstage-jupyter --port 8889

# Pick the agent right from the launcher (same spec format as every
# LangStage stage; sets LANGSTAGE_AGENT_SPEC for you)
langstage-jupyter -a my_agent.py:graph

# No agent or API key yet? Launch with the keyless demo agent
langstage-jupyter --demo

# Print the resolved configuration (each value, its source, and the
# env var / langstage.toml key that sets it) and exit
langstage-jupyter --show-config
```

### Alternative: Manual Configuration

If you prefer manual control or need to use `jupyter lab` directly, you can set the environment variables yourself:

1. **Configure environment variables** (create a `.env` file or export):

```bash
# Required: Jupyter server configuration
export LANGSTAGE_JUPYTER_SERVER_URL=http://localhost:8888
export LANGSTAGE_JUPYTER_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# If using the default agent, set your API key
export ANTHROPIC_API_KEY=your-api-key-here
```

2. **Start JupyterLab** with matching configuration:

```bash
jupyter lab --port 8888 --IdentityProvider.token=$LANGSTAGE_JUPYTER_TOKEN
```

**Important:** The server URL and token must match between your environment variables and JupyterLab's startup parameters.

## Using Custom Agents

langstage-jupyter is designed to work with any langgraph-compatible agent. You can easily use your own langgraph-compatible agents instead of the default agent.

### Creating a Custom Agent

Create a file with your agent (e.g., `my_agent.py`):

```python
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langgraph.checkpoint.memory import MemorySaver
import os

# The agent automatically discovers the workspace
workspace = os.getenv('LANGSTAGE_WORKSPACE_ROOT', '.')

# Create your custom agent
agent = create_deep_agent(
    name="my-custom-agent",  # Optional: name shown in chat interface
    model="anthropic:claude-sonnet-4-20250514",
    backend=FilesystemBackend(root_dir=workspace, virtual_mode=True),
    checkpointer=MemorySaver(),
    tools=[...your_custom_tools...]
)
```

### Configuring the Extension to Use Your Agent

Set the `LANGSTAGE_AGENT_SPEC` environment variable to point to your agent:

```bash
# Format: path/to/file.py:variable_name
export LANGSTAGE_AGENT_SPEC=./my_agent.py:agent
```

Then launch as normal:

```bash
# With the launcher (recommended)
langstage-jupyter

# Or manually
jupyter lab --port 8888 --IdentityProvider.token=$LANGSTAGE_JUPYTER_TOKEN
```

The chat interface will automatically display your custom agent's name (if you set the `name` attribute).

### Agent Portability

Agents configured for langstage-jupyter work seamlessly with every other LangStage stage:

```bash
# Same configuration works everywhere!
export LANGSTAGE_AGENT_SPEC=./my_agent.py:agent
export LANGSTAGE_WORKSPACE_ROOT=/path/to/project

# Run in JupyterLab
langstage-jupyter

# Or in the browser / terminal
langstage run
langstage-cli
```

## Environment Variables

All configuration uses the `LANGSTAGE_` prefix (the pre-rename `DEEPAGENT_` names still resolve as deprecated fallbacks):

| Variable | Purpose | Default | When to Set |
|----------|---------|---------|-------------|
| `LANGSTAGE_AGENT_SPEC` | Custom agent location (`path:variable`) | Uses default agent | Optional: for custom agents |
| `LANGSTAGE_WORKSPACE_ROOT` | Working directory for agent | JupyterLab root | Optional |
| `LANGSTAGE_JUPYTER_SERVER_URL` | Jupyter server URL | Auto-detected | Manual config only |
| `LANGSTAGE_JUPYTER_TOKEN` | Jupyter auth token | Auto-generated | Manual config only |
| `ANTHROPIC_API_KEY` | Anthropic API key | None | Required for default agent |

When using the `langstage-jupyter` launcher, `LANGSTAGE_JUPYTER_SERVER_URL` and `LANGSTAGE_JUPYTER_TOKEN` are automatically configured and don't need to be set.

See [.env.example](https://github.com/dkedar7/langstage-jupyter/blob/main/.env.example) for a complete configuration template.

## Interface Controls

- **⟳ Reload**: Reload your agent without restarting JupyterLab (useful during agent development)
- **Clear**: Start a new conversation thread
- **Status Indicator**:
  - 🟢 Green: Agent ready
  - 🟠 Orange: Agent loading
  - 🔴 Red: Agent error

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

MIT License - see [LICENSE](LICENSE) for details.
