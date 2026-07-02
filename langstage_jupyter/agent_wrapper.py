"""
Wrapper for LangGraph agent to provide a consistent API for the extension.
"""
import importlib
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from dotenv import find_dotenv, load_dotenv
# Resolve .env from the user's working (launch) directory, not the installed
# package location. A bare load_dotenv() searches upward from this module inside
# site-packages, so the user's project .env is never found and silently ignored.
# (gh #32)
load_dotenv(find_dotenv(usecwd=True))

# Import configuration.
from . import config
from langstage_core import load_agent_spec


class AgentWrapper:
    """Wrapper class for LangGraph agent."""

    def __init__(self, agent_module_path: Optional[str] = None, agent_variable_name: Optional[str] = None):
        """
        Initialize the agent wrapper.

        Priority for agent resolution:
        1. DEEPAGENT_AGENT_SPEC environment variable (format: "module_or_file:variable")
        2. Function parameters (agent_module_path, agent_variable_name)
        3. Default values from config module

        Args:
            agent_module_path: Path to the module or file containing the agent.
                              Can be a module path (e.g., "my_package.agent")
                              or a file path (e.g., "./my_agent.py" or "/abs/path/agent.py")
                              Defaults to config.AGENT_MODULE if not provided.
            agent_variable_name: Name of the variable to load from the module.
                                Defaults to None (will try 'agent' then 'graph').
        """
        self.agent = None

        # Check for environment variable spec
        agent_spec = config.AGENT_SPEC

        if agent_spec:
            # Parse "module_or_file:variable" format
            parts = agent_spec.split(':', 1)
            if len(parts) == 2:
                self.agent_module_path = parts[0]
                self.agent_variable_name = parts[1]
                print(f"Using agent from environment: {self.agent_module_path}:{self.agent_variable_name}")
            else:
                print(f"Warning: DEEPAGENT_AGENT_SPEC format should be 'module:variable', got: {agent_spec}")
                print(f"Falling back to parameters or defaults")
                self.agent_module_path = agent_module_path or config.AGENT_MODULE
                self.agent_variable_name = agent_variable_name or config.AGENT_VARIABLE
        else:
            # Use function parameters or config defaults
            self.agent_module_path = agent_module_path or config.AGENT_MODULE
            self.agent_variable_name = agent_variable_name or config.AGENT_VARIABLE

        self._load_agent()
        # The root the agent's backend was just built from (config.WORKSPACE_ROOT,
        # else cwd "."). set_root_dir() compares against this so it only rebuilds
        # when JupyterLab's live root actually differs. (gh #36)
        self._applied_root = self._resolve_root(
            str(config.WORKSPACE_ROOT) if config.WORKSPACE_ROOT else "."
        )

    def _load_agent(self):
        """Load the agent via the shared host loader.

        Builds a ``module_or_path:variable`` spec from the resolved module
        path + variable name and delegates to
        ``langstage_core.host.load_agent_spec`` (which handles both
        file paths and dotted module paths). When no explicit variable name
        was requested, falls back from ``agent`` to ``graph`` — preserving the
        extension's historical default-name behavior.
        """
        # Invalidate any cached AG-UI wrapper so it rebuilds around the fresh graph.
        self._agui_agent = None
        var = self.agent_variable_name or "agent"
        try:
            self.agent = load_agent_spec(f"{self.agent_module_path}:{var}")
            print(f"Loaded agent: {self.agent_module_path}:{var}")
        except (ValueError, FileNotFoundError, ImportError, AttributeError) as e:
            # No explicit variable requested → try the legacy 'graph' fallback.
            if self.agent_variable_name is None:
                try:
                    self.agent = load_agent_spec(f"{self.agent_module_path}:graph")
                    print(f"Loaded agent: {self.agent_module_path}:graph")
                    return
                except Exception:
                    pass
            print(f"Warning: Could not load agent '{self.agent_module_path}': {e}")
            if config.AGENT_SPEC:
                print(f"Note: DEEPAGENT_AGENT_SPEC is set to: {config.AGENT_SPEC}")
            self.agent = None
        except Exception as e:
            print(f"Error loading agent: {e}")
            if config.DEBUG:
                import traceback
                traceback.print_exc()
            self.agent = None

    def reload_agent(self):
        """Reload the agent module (useful for development)."""
        # Clear the module from sys.modules if it's there
        # Clear the agent module (and its submodules) from sys.modules so the next
        # load re-imports it. Match exactly or on a dotted-submodule prefix — a
        # bare substring check also nuked unrelated modules (the module path
        # 'langstage_jupyter.agent' is a substring of '...agent_wrapper'). (gh #36)
        path = self.agent_module_path
        modules_to_remove = [
            mod_name for mod_name in sys.modules
            if (mod_name == path
                or mod_name.startswith(path + ".")
                or mod_name.startswith('custom_agent_'))
        ]
        for mod_name in modules_to_remove:
            del sys.modules[mod_name]

        # Reload
        self._load_agent()

    @staticmethod
    def _resolve_root(root: str) -> str:
        """Normalize a root path for change comparison (absolute, expanded)."""
        try:
            return str(Path(root).expanduser().resolve())
        except Exception:
            return str(root)

    def set_root_dir(self, root_dir: str):
        """
        Re-point the agent's filesystem backend at JupyterLab's live root.
        Also publishes the workspace root as an environment variable for agents
        to discover. Called on every chat message; the agent is rebuilt only when
        the resolved root actually changes.

        Args:
            root_dir: The root directory path (JupyterLab launch directory)
        """
        # Publish BOTH the canonical and the legacy env name. The README's own
        # custom-agent example reads canonical `LANGSTAGE_WORKSPACE_ROOT`, so a
        # user following the docs verbatim would otherwise see "." instead of
        # the live JupyterLab root — the agent's read path was renamed (0.5.4)
        # but this write path still published only the deprecated name.
        # (gh #-dogfood)
        os.environ['LANGSTAGE_WORKSPACE_ROOT'] = root_dir
        os.environ['DEEPAGENT_WORKSPACE_ROOT'] = root_dir

        # Re-root only on an actual change — set_root_dir runs on every message,
        # and rebuilding each time would be wasteful and reset agent state.
        resolved = self._resolve_root(root_dir)
        if resolved == getattr(self, "_applied_root", None):
            return
        self._applied_root = resolved

        # Fast path: an agent exposing a mutable filesystem backend is re-pointed
        # in place. deepagents' CompiledStateGraph does NOT expose `.backend`, so
        # this is for custom agents / future deepagents. (The old import path,
        # `deepagents.tools.filesystem`, never existed — it's `deepagents.backends`.)
        if self.agent is not None and hasattr(self.agent, 'backend'):
            try:
                from deepagents.backends import FilesystemBackend
                self.agent.backend = FilesystemBackend(
                    root_dir=root_dir,
                    virtual_mode=config.VIRTUAL_MODE,
                )
                print(f"Set agent backend root_dir to: {root_dir}")
                return
            except ImportError:
                pass
            except Exception as e:
                print(f"Warning: Could not set agent backend root_dir: {e}")

        # General path (the bundled default and `create_deep_agent` agents): their
        # FilesystemBackend is built from `config.WORKSPACE_ROOT` at import time, so
        # refresh that resolved value and rebuild the agent — its backend then
        # re-roots at the live directory. Previously this branch was dead (wrong
        # import + a guard that's never true for a CompiledStateGraph), so the
        # documented re-root never happened. (gh #36)
        try:
            config.WORKSPACE_ROOT = Path(resolved)
            self.reload_agent()
            print(f"Re-rooted agent filesystem to: {root_dir}")
        except Exception as e:
            print(f"Warning: Could not re-root agent to {root_dir}: {e}")

    def _append_context_to_message(self, message: str, context: Optional[Dict[str, Any]]) -> str:
        """
        Append context information to the message.

        Args:
            message: The original user message
            context: Context dict with current_directory, focused_widget, selected_text, and selection_metadata

        Returns:
            Message with appended context
        """
        if not context:
            return message

        context_parts = []
        if context.get("current_directory"):
            context_parts.append(f"Current directory: {context['current_directory']}")
        if context.get("focused_widget"):
            focused = context['focused_widget']
            # Check if it's a file path or special widget
            if '/' in focused or focused.endswith(('.ipynb', '.py', '.txt', '.md')):
                context_parts.append(f"Currently focused file: {focused}")
            else:
                context_parts.append(f"Currently focused: {focused}")
        if context.get("selected_text"):
            selected = context['selected_text']
            selection_metadata = context.get("selection_metadata", "")

            # Truncate very long selections to avoid token bloat
            max_length = 2000
            if len(selected) > max_length:
                selected = selected[:max_length] + f"\n... (truncated, {len(selected) - max_length} more characters)"

            # Format location information
            location_info = ""
            if selection_metadata:
                if selection_metadata.startswith("cell_"):
                    cell_idx = selection_metadata.replace("cell_", "")
                    location_info = f" from cell index {cell_idx}"
                elif selection_metadata.startswith("line_"):
                    line_num = selection_metadata.replace("line_", "")
                    location_info = f" from line {line_num}"
                elif selection_metadata.startswith("lines_"):
                    line_range = selection_metadata.replace("lines_", "")
                    location_info = f" from lines {line_range}"

            context_parts.append(f"User has selected the following text{location_info}:\n```\n{selected}\n```")

        if context_parts:
            return f"{message}\n\n" + "\n".join(context_parts)
        return message

    def execute(
        self,
        message: Optional[str] = None,
        decisions: Optional[list] = None,
        config: Optional[Dict[str, Any]] = None,
        thread_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> Iterator[Dict[str, Any]]:
        """
        Execute the agent with either a message or resume from interrupt.

        This unified method handles both regular streaming and interrupt resumption,
        providing true decoupling from LangGraph-specific abstractions.

        Args:
            message: Optional user message to send to the agent
            decisions: Optional list of decisions to resume from interrupt
            config: Optional configuration for the agent
            thread_id: Optional thread ID for conversation history
            context: Optional context with current_directory and focused_widget

        Yields:
            Dict containing chunks of the agent's response

        Note:
            Must provide exactly one of: message or decisions
        """
        if self.agent is None:
            error_msg = "Agent not loaded. "
            if os.environ.get('JUPYTER_AGENT_PATH'):
                error_msg += f"Check JUPYTER_AGENT_PATH: {os.environ.get('JUPYTER_AGENT_PATH')}"
            else:
                error_msg += "Please create agent.py with your LangGraph agent or set JUPYTER_AGENT_PATH."
            yield {
                "error": error_msg,
                "status": "error"
            }
            return

        # Prepare config with thread_id if provided
        agent_config = config or {}
        if thread_id:
            agent_config["configurable"] = agent_config.get("configurable", {})
            agent_config["configurable"]["thread_id"] = thread_id

        # Resolve inputs before building the agent, so a bad call fails fast.
        tid = "jupyter"
        if isinstance(agent_config, dict):
            tid = agent_config.get("configurable", {}).get("thread_id", "jupyter")

        if message is not None:
            agui_msg = self._append_context_to_message(message, context)
            resume = None
        elif decisions is not None:
            agui_msg, resume = "", {"decisions": decisions}
        else:
            yield {"error": "Must provide either 'message' or 'decisions'", "status": "error"}
            return

        # Since core 1.0 (ADR 0003) streaming routes ONLY through the in-process
        # AG-UI adapter, yielding the SAME chunk shape the frontend consumes.
        from .agui_stream import build_session_agent, stream_updates_sync

        try:
            if self._agui_agent is None:
                self._agui_agent = build_session_agent(self.agent)
        except RuntimeError as e:
            yield {"error": str(e), "status": "error"}
            return

        try:
            for chunk in stream_updates_sync(self._agui_agent, agui_msg, tid, resume=resume):
                yield chunk
        except Exception as e:
            yield {"error": f"Error executing agent: {str(e)}", "status": "error"}

    def resume_from_interrupt(self, decisions: list, config: Optional[Dict[str, Any]] = None, thread_id: Optional[str] = None) -> Iterator[Dict[str, Any]]:
        """
        Resume execution after a human-in-the-loop interrupt.

        This is a convenience wrapper around execute() for backward compatibility.

        Args:
            decisions: List of decision objects with 'type' and optional fields
            config: Optional configuration for the agent
            thread_id: Thread ID to resume

        Yields:
            Dict containing chunks of the agent's response
        """
        for chunk in self.execute(decisions=decisions, config=config, thread_id=thread_id):
            yield chunk

    def stream(self, message: str, config: Optional[Dict[str, Any]] = None, thread_id: Optional[str] = None, context: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
        """
        Stream responses from the agent.

        This is a convenience wrapper around execute() for backward compatibility.

        Args:
            message: The user message to send to the agent
            config: Optional configuration for the agent
            thread_id: Optional thread ID for conversation history
            context: Optional context with current_directory and focused_notebook

        Yields:
            Dict containing chunks of the agent's response
        """
        for chunk in self.execute(message=message, config=config, thread_id=thread_id, context=context):
            yield chunk


# Global agent instance
_agent_instance: Optional[AgentWrapper] = None


def get_agent() -> AgentWrapper:
    """Get or create the global agent instance."""
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = AgentWrapper()
    return _agent_instance
