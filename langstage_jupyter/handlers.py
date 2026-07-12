"""
HTTP request handlers for the DeepAgents extension.
"""
import asyncio
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict

from jupyter_server.base.handlers import APIHandler
from jupyter_server.utils import url_path_join
import tornado
from tornado.web import HTTPError

from .agent_wrapper import get_agent

# Thread pool for running blocking agent operations
_executor = ThreadPoolExecutor(max_workers=4)

# Track active executions and their cancellation flags
_active_executions: Dict[str, threading.Event] = {}
_execution_lock = threading.Lock()

# The standard env var each model provider needs — for a cheap credential preflight so
# the sidebar doesn't report "ready" for the bundled default agent when its key is
# missing and the first turn would fail with a raw provider auth error (gh #60).
_PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
    "google_vertexai": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistralai": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "together": "TOGETHER_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


def _missing_provider_key(model_name: str) -> Optional[str]:
    """The provider key ``model_name`` needs, if it is absent from the environment.

    Pure over ``model_name`` (the provider is the part before ``:``). Shared by the
    ``/health`` default-agent readiness check and the launcher's ``--verify`` preflight
    so both surfaces name the *same* variable for the same failure, instead of one dumping
    a raw provider auth error (gh #60, gh #66). Returns ``None`` when the provider is
    unknown or the key is already set.
    """
    provider = model_name.split(":", 1)[0].lower() if ":" in model_name else ""
    env_var = _PROVIDER_KEY_ENV.get(provider)
    if env_var and not os.environ.get(env_var):
        return env_var
    return None


def _missing_default_agent_key() -> Optional[str]:
    """The provider key the BUNDLED default agent needs, if it is absent.

    The default agent's model is built lazily via ``init_chat_model(MODEL_NAME)``, so the
    object constructs fine with no key and only fails at the first API call — which is why
    readiness (`agent is not None`) read "ready" while the first turn died with a provider
    auth error (gh #60). A custom/BYO agent's credentials are the operator's concern, so
    this preflight is scoped to the default agent, where the model spec (and thus the
    required key) is known.
    """
    from . import config

    if config.AGENT_SPEC:  # a custom agent was configured -> not our concern
        return None
    return _missing_provider_key((getattr(config, "MODEL_NAME", "") or "").strip())


def _agent_readiness():
    """Assess whether the loaded agent can actually run a turn (gh #60, #69).

    Returns ``(status, ready, message)``. "loaded" (`agent is not None`) is not enough:
    an uncompiled graph loads but has no ``astream``, and the default agent loads without
    its provider key but fails on the first turn. Readiness reflects both.
    """
    agent = get_agent()
    obj = getattr(agent, "agent", None)
    if obj is None:
        return "agent_not_loaded", False, "Agent module not found or failed to load"
    if not callable(getattr(obj, "astream", None)):
        return (
            "not_runnable",
            False,
            "Loaded, but not a runnable graph — export a compiled graph (call .compile()).",
        )
    missing = _missing_default_agent_key()
    if missing:
        return (
            "needs_setup",
            False,
            f"Loaded, but {missing} is not set — the first turn will fail. Set it and reload.",
        )
    return "healthy", True, "Agent is ready"


class ChatHandler(APIHandler):
    """Handler for chat messages."""

    @tornado.web.authenticated
    async def post(self):
        """
        Handle POST requests to send a message to the agent.

        Expected JSON payload:
        {
            "message": "user message",
            "stream": false,  // optional, default false
            "thread_id": "uuid"  // optional, for conversation history
        }
        """
        try:
            # Parse request body
            data = self.get_json_body()
            # get_json_body() returns None for an EMPTY body (it only raises on
            # invalid JSON), so guard before .get() or an empty POST 500s. (gh #53)
            if data is None:
                raise HTTPError(400, "Request body must be a JSON object")
            message = data.get("message")
            thread_id = data.get("thread_id")
            current_directory = data.get("current_directory", "")
            focused_widget = data.get("focused_widget", "")
            selected_text = data.get("selected_text", "")
            selection_metadata = data.get("selection_metadata", "")

            if not message:
                raise HTTPError(400, "Message is required")

            # Get root directory from server settings
            root_dir = self.settings.get("server_root_dir", "")

            # Get agent instance and set root directory
            agent = get_agent()
            agent.set_root_dir(root_dir)

            # Create context object
            context = {
                "current_directory": current_directory,
                "focused_widget": focused_widget,
                "selected_text": selected_text,
                "selection_metadata": selection_metadata
            }

            # Stream response
            self.set_header("Content-Type", "text/event-stream")
            self.set_header("Cache-Control", "no-cache")
            self.set_header("Connection", "keep-alive")

            # Run agent.stream() in thread pool to avoid blocking the event loop
            queue = asyncio.Queue()
            loop = asyncio.get_event_loop()

            # Create cancellation event for this execution
            cancel_event = threading.Event()
            if thread_id:
                with _execution_lock:
                    _active_executions[thread_id] = cancel_event

            def run_agent_stream():
                """Run the blocking agent.stream() in a thread."""
                try:
                    for chunk in agent.stream(message, thread_id=thread_id, context=context):
                        # Check if execution was cancelled
                        if cancel_event.is_set():
                            asyncio.run_coroutine_threadsafe(
                                queue.put({"status": "cancelled", "message": "Execution cancelled by user"}),
                                loop
                            )
                            asyncio.run_coroutine_threadsafe(queue.put(None), loop)
                            return

                        # Send chunk to async handler via queue
                        asyncio.run_coroutine_threadsafe(queue.put(chunk), loop)
                    # Signal completion
                    asyncio.run_coroutine_threadsafe(queue.put(None), loop)
                except Exception as e:
                    # Send error to async handler
                    asyncio.run_coroutine_threadsafe(queue.put(e), loop)
                finally:
                    # Clean up execution tracking
                    if thread_id:
                        with _execution_lock:
                            _active_executions.pop(thread_id, None)

            # Submit to thread pool
            _executor.submit(run_agent_stream)

            # Stream chunks from queue
            while True:
                chunk = await queue.get()

                # Check for completion signal
                if chunk is None:
                    break

                # Check for error
                if isinstance(chunk, Exception):
                    raise chunk

                # Send as server-sent event
                event_data = f"data: {json.dumps(chunk)}\n\n"
                self.write(event_data)
                await self.flush()

            self.finish()

        except HTTPError:
            raise
        except Exception as e:
            self.log.error(f"Error in ChatHandler: {e}", exc_info=True)
            raise HTTPError(500, str(e))


class ReloadAgentHandler(APIHandler):
    """Handler to reload the agent module."""

    @tornado.web.authenticated
    async def post(self):
        """Reload the agent module."""
        try:
            agent = get_agent()
            agent.reload_agent()
            self.finish(json.dumps({
                "status": "success",
                "message": "Agent reloaded successfully"
            }))
        except Exception as e:
            self.log.error(f"Error reloading agent: {e}", exc_info=True)
            raise HTTPError(500, str(e))


class ResumeHandler(APIHandler):
    """Handler to resume execution after a human-in-the-loop interrupt."""

    @tornado.web.authenticated
    async def post(self):
        """
        Handle POST requests to resume from an interrupt.

        Expected JSON payload:
        {
            "decisions": [{"type": "approve"}, ...],
            "thread_id": "uuid"
        }
        """
        try:
            data = self.get_json_body()
            if data is None:  # empty body -> 400, not a 500 on None.get() (gh #53)
                raise HTTPError(400, "Request body must be a JSON object")
            decisions = data.get("decisions", [])
            thread_id = data.get("thread_id")

            if not thread_id:
                raise HTTPError(400, "thread_id is required")

            agent = get_agent()

            # Stream response
            self.set_header("Content-Type", "text/event-stream")
            self.set_header("Cache-Control", "no-cache")
            self.set_header("Connection", "keep-alive")

            # Run agent.resume_from_interrupt() in thread pool to avoid blocking
            queue = asyncio.Queue()
            loop = asyncio.get_event_loop()

            # Create cancellation event for this execution
            cancel_event = threading.Event()
            if thread_id:
                with _execution_lock:
                    _active_executions[thread_id] = cancel_event

            def run_agent_resume():
                """Run the blocking agent.resume_from_interrupt() in a thread."""
                try:
                    for chunk in agent.resume_from_interrupt(decisions, thread_id=thread_id):
                        # Check if execution was cancelled
                        if cancel_event.is_set():
                            asyncio.run_coroutine_threadsafe(
                                queue.put({"status": "cancelled", "message": "Execution cancelled by user"}),
                                loop
                            )
                            asyncio.run_coroutine_threadsafe(queue.put(None), loop)
                            return

                        # Send chunk to async handler via queue
                        asyncio.run_coroutine_threadsafe(queue.put(chunk), loop)
                    # Signal completion
                    asyncio.run_coroutine_threadsafe(queue.put(None), loop)
                except Exception as e:
                    # Send error to async handler
                    asyncio.run_coroutine_threadsafe(queue.put(e), loop)
                finally:
                    # Clean up execution tracking
                    if thread_id:
                        with _execution_lock:
                            _active_executions.pop(thread_id, None)

            # Submit to thread pool
            _executor.submit(run_agent_resume)

            # Stream chunks from queue
            while True:
                chunk = await queue.get()

                # Check for completion signal
                if chunk is None:
                    break

                # Check for error
                if isinstance(chunk, Exception):
                    raise chunk

                # Send as server-sent event
                event_data = f"data: {json.dumps(chunk)}\n\n"
                self.write(event_data)
                await self.flush()

            self.finish()

        except HTTPError:
            raise
        except Exception as e:
            self.log.error(f"Error in ResumeHandler: {e}", exc_info=True)
            raise HTTPError(500, str(e))


class HealthHandler(APIHandler):
    """Handler to check if the agent is loaded."""

    @tornado.web.authenticated
    async def get(self):
        """Report readiness — whether the agent can actually run a turn, not merely
        whether the object loaded (gh #60). ``ready`` gates the sidebar 🟢 indicator;
        ``agent_loaded`` stays accurate (the object is present) for back-compat.
        """
        try:
            agent = get_agent()
            is_loaded = agent.agent is not None
            status, ready, message = _agent_readiness()

            # Try to get agent name if available
            agent_name = None
            if is_loaded and hasattr(agent.agent, "name"):
                agent_name = agent.agent.name

            response = {
                "status": status,
                "agent_loaded": is_loaded,
                "ready": ready,
                "message": message,
            }
            if agent_name:
                response["agent_name"] = agent_name

            self.finish(json.dumps(response))
        except Exception as e:
            self.log.error(f"Error checking health: {e}", exc_info=True)
            raise HTTPError(500, str(e))


class CancelHandler(APIHandler):
    """Handler to cancel ongoing agent execution."""

    @tornado.web.authenticated
    async def post(self):
        """
        Cancel an ongoing agent execution.

        Expected JSON payload:
        {
            "thread_id": "uuid"
        }
        """
        try:
            data = self.get_json_body()
            if data is None:  # empty body -> 400, not a 500 on None.get() (gh #53)
                raise HTTPError(400, "Request body must be a JSON object")
            thread_id = data.get("thread_id")

            if not thread_id:
                raise HTTPError(400, "thread_id is required")

            # Set the cancellation event for this thread
            with _execution_lock:
                cancel_event = _active_executions.get(thread_id)
                if cancel_event:
                    cancel_event.set()
                    self.finish(json.dumps({
                        "status": "success",
                        "message": "Cancellation requested"
                    }))
                else:
                    self.finish(json.dumps({
                        "status": "not_found",
                        "message": "No active execution found for this thread"
                    }))

        except HTTPError:
            raise
        except Exception as e:
            self.log.error(f"Error in CancelHandler: {e}", exc_info=True)
            raise HTTPError(500, str(e))


def setup_handlers(web_app):
    """Setup the HTTP request handlers."""
    host_pattern = ".*$"
    base_url = web_app.settings["base_url"]

    # Define route patterns
    route_pattern_chat = url_path_join(base_url, "langstage-jupyter", "chat")
    route_pattern_reload = url_path_join(base_url, "langstage-jupyter", "reload")
    route_pattern_resume = url_path_join(base_url, "langstage-jupyter", "resume")
    route_pattern_health = url_path_join(base_url, "langstage-jupyter", "health")
    route_pattern_cancel = url_path_join(base_url, "langstage-jupyter", "cancel")

    # Add handlers
    handlers = [
        (route_pattern_chat, ChatHandler),
        (route_pattern_reload, ReloadAgentHandler),
        (route_pattern_resume, ResumeHandler),
        (route_pattern_health, HealthHandler),
        (route_pattern_cancel, CancelHandler),
    ]

    web_app.add_handlers(host_pattern, handlers)
