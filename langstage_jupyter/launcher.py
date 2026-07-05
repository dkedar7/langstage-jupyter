#!/usr/bin/env python3
"""
DeepAgent Lab launcher script.

This script wraps the 'jupyter lab' command to automatically configure
the Jupyter server settings and make them available to agents.

Usage:
    langstage-jupyter [options] [jupyter lab args...]

Example:
    langstage-jupyter --port 8889
    langstage-jupyter --no-browser
    langstage-jupyter -a my_agent.py:graph     # pick the agent, same spec format
                                           # as every deep-agent surface
    langstage-jupyter --demo                   # keyless demo agent, no API key
    langstage-jupyter --show-config            # print resolved config and exit
"""
import importlib.util
import os
import sys
import socket
import secrets
import subprocess

# The keyless echo agent shipped with the shared core — see `--demo`.
DEMO_AGENT_SPEC = "langstage_core.demo.stub:graph"

_LAUNCHER_HELP = """\
langstage-jupyter - launch JupyterLab with the LangStage chat sidebar.

Usage:
  langstage-jupyter [launcher options] [jupyter lab options...]

Launcher options:
  -a, --agent SPEC   Agent to load (module:attr or path/to/file.py:attr).
  --demo             Use the built-in keyless demo agent (no API key).
  --show-config      Print the resolved configuration and exit.
  --verify           Preflight the agent (run one real turn); exit 0/1. Then exit.
  --serve-check      Headless HTTP smoke test: boot the server extension, serve one
                     turn over /langstage-jupyter/chat, exit 0/1. Then exit.
  --version, -V      Print the langstage-jupyter version and exit.
  -h, --help         Show this message and exit.

All other options are passed through to `jupyter lab`
(run `jupyter lab --help` to see those)."""


def ensure_jupyterlab():
    """Fail fast with an actionable hint if JupyterLab isn't importable.

    JupyterLab is a declared runtime dependency, but a user on an older/odd
    install may still lack it. The launcher runs ``jupyter lab``; if JupyterLab
    is absent the ``jupyter`` dispatcher (shipped by ``jupyter_server``) is
    *present* and simply prints its help + ``jupyter-lab not found`` and exits
    non-zero — it does **not** raise ``FileNotFoundError``. So the old
    ``except FileNotFoundError`` guard never fired and the user got a cryptic
    help dump instead of guidance (gh #24). Pre-checking the import is the
    reliable signal.
    """
    if importlib.util.find_spec("jupyterlab") is None:
        print(
            "ERROR: JupyterLab is not installed, so `jupyter lab` cannot start.\n"
            "  Install it with:\n"
            "    pip install jupyterlab\n"
            "  (or reinstall this package, which now depends on it: "
            "pip install --upgrade langstage-jupyter)"
        )
        sys.exit(1)


def extract_agent_args(args):
    """Split our agent flags out of the passthrough jupyter-lab args.

    Handles ``-a SPEC`` / ``--agent SPEC`` / ``--agent=SPEC`` and ``--demo``.
    Returns ``(agent_spec, demo, remaining_args)`` — remaining_args go to
    ``jupyter lab`` untouched.
    """
    agent_spec = None
    demo = False
    remaining = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-a", "--agent") and i + 1 < len(args):
            agent_spec = args[i + 1]
            i += 2
            continue
        if arg.startswith("--agent="):
            agent_spec = arg.split("=", 1)[1]
            i += 1
            continue
        if arg == "--demo":
            demo = True
            i += 1
            continue
        remaining.append(arg)
        i += 1
    return agent_spec, demo, remaining


def find_available_port(start_port=8888, max_attempts=10):
    """Find an available port starting from start_port."""
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"Could not find available port in range {start_port}-{start_port + max_attempts}")


def generate_token():
    """Generate a secure random token for Jupyter authentication."""
    return secrets.token_urlsafe(32)


# ── --serve-check: headless HTTP smoke test of the deployed extension ──
#
# The served route prefix the extension registers (handlers.setup_handlers).
SERVE_CHECK_ROUTE = "langstage-jupyter"


def _summarize_sse(lines):
    """Reduce a ``/chat`` SSE stream to ``(chunk_count, saw_complete, error)``.

    Pure so it is unit-testable without booting a server. ``lines`` is any
    iterable of raw SSE lines; only ``data: {json}`` lines carry frames. A frame
    with a non-empty ``chunk`` counts; ``{"status": "complete"}`` ends it cleanly;
    ``{"status": "error"}`` (or an ``error`` key) captures the failure message.
    """
    import json as _json

    chunk_count = 0
    saw_complete = False
    error = None
    for raw in lines:
        if isinstance(raw, bytes):  # urllib streams bytes lines; tests pass str
            raw = raw.decode("utf-8", "replace")
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        try:
            frame = _json.loads(line[len("data:"):].strip())
        except ValueError:
            continue
        if not isinstance(frame, dict):
            continue
        if frame.get("chunk"):
            chunk_count += 1
        if frame.get("status") == "complete":
            saw_complete = True
        if frame.get("status") == "error" or frame.get("error"):
            error = frame.get("error") or frame.get("message") or "agent error"
    return chunk_count, saw_complete, error


def serve_check(agent_spec=None, *, boot_timeout=45.0, turn_timeout=60.0):
    """Boot the extension headlessly, drive one served turn, return an exit code.

    The HTTP counterpart of ``--verify`` (ADR 0004): ``--verify`` proves the *agent
    object* completes a turn but never touches the server extension, so a route/
    registration/handler regression (e.g. the gh #53 empty-body 500) passes it while
    the *served* endpoint is broken. This boots a real ``jupyter server`` (the server
    extension only — no browser/frontend needed to exercise the handler surface),
    polls ``/{route}/health`` until the agent is loaded, POSTs one turn to
    ``/{route}/chat`` and asserts the SSE stream yields >=1 non-empty ``chunk`` and
    ends with ``{"status": "complete"}``, then tears the server down.

    ``agent_spec`` defaults to the keyless demo agent so it runs in CI with no API
    key; pass a spec (``-a``) to smoke-test a real agent end-to-end over HTTP.
    Returns ``0`` (served turn verified) or ``1`` (any failure), printing a one-line
    verdict either way.
    """
    import json
    import time
    import urllib.error
    import urllib.request

    spec = (agent_spec or "").strip() or DEMO_AGENT_SPEC
    port = find_available_port()
    token = generate_token()
    base = f"http://localhost:{port}/{SERVE_CHECK_ROUTE}"

    env = dict(os.environ)
    # The agent under test, and the callback URL/token its notebook tools use.
    env["LANGSTAGE_AGENT_SPEC"] = env["DEEPAGENT_AGENT_SPEC"] = spec
    env["LANGSTAGE_JUPYTER_SERVER_URL"] = env["DEEPAGENT_JUPYTER_SERVER_URL"] = f"http://localhost:{port}"
    env["LANGSTAGE_JUPYTER_TOKEN"] = env["DEEPAGENT_JUPYTER_TOKEN"] = token

    def _request(path, data=None, timeout=10.0):
        headers = {"Authorization": f"token {token}"}
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode()
        req = urllib.request.Request(base + path, data=body, headers=headers)
        return urllib.request.urlopen(req, timeout=timeout)

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "jupyter_server",
            "--no-browser",
            f"--ServerApp.port={port}",
            f"--ServerApp.token={token}",
            "--ServerApp.open_browser=False",
            # Local ephemeral smoke-test server; token auth already gates it and
            # exempts XSRF, but disable the check so the POST can't 403 on it.
            "--ServerApp.disable_check_xsrf=True",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        # 1. Poll health until the agent is loaded (server boot + agent import).
        deadline = time.monotonic() + boot_timeout
        health = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:  # server died before serving
                print("[fail] serve-check: jupyter server exited before it was ready "
                      f"(code {proc.returncode})")
                return 1
            try:
                health = json.loads(_request("/health", timeout=3.0).read())
                if health.get("agent_loaded"):
                    break
            except (urllib.error.URLError, ConnectionError, OSError, ValueError):
                pass  # not up yet
            time.sleep(0.5)
        if not (health and health.get("agent_loaded")):
            print(f"[fail] serve-check: agent never became ready within {boot_timeout:.0f}s "
                  f"(last health: {health})")
            return 1

        # 2. Drive one served turn and inspect the SSE stream.
        try:
            resp = _request(
                "/chat", data={"message": "serve-check ping", "thread_id": "serve-check"},
                timeout=turn_timeout,
            )
            chunks, complete, error = _summarize_sse(iter(resp))
        except urllib.error.HTTPError as e:
            print(f"[fail] serve-check: POST /{SERVE_CHECK_ROUTE}/chat returned HTTP {e.code} "
                  f"({e.reason})")
            return 1
        except (urllib.error.URLError, OSError) as e:
            print(f"[fail] serve-check: POST /{SERVE_CHECK_ROUTE}/chat failed: {e}")
            return 1

        if error is not None:
            print(f"[fail] serve-check: the served turn errored: {error}")
            return 1
        if chunks < 1 or not complete:
            print(f"[fail] serve-check: incomplete turn "
                  f"(streamed {chunks} chunk(s), complete={complete})")
            return 1

        name = health.get("agent_name") or spec
        print(f"[ ok ] served turn verified: agent={name!r}, streamed {chunks} chunks, "
              f"completed cleanly (routes under /{SERVE_CHECK_ROUTE}/)")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - best-effort teardown
            proc.kill()


def main():
    """Main launcher function."""
    # Parse command line arguments
    args = sys.argv[1:]

    # --help / -h: show the LAUNCHER's own flags. Otherwise --help passes through
    # to `jupyter lab`, which dumps JupyterLab's help and never mentions
    # --demo / -a / --show-config / --version (gh #-dogfood).
    if "--help" in args or "-h" in args:
        print(_LAUNCHER_HELP)
        return

    # --version: report THIS package's version and exit. Passing it through to
    # `jupyter lab` printed JupyterLab's version instead (gh #-dogfood).
    if "--version" in args or "-V" in args:
        from importlib.metadata import PackageNotFoundError, version

        try:
            print(f"langstage-jupyter {version('langstage-jupyter')}")
        except PackageNotFoundError:  # pragma: no cover
            print("langstage-jupyter 0.0.0+local")
        return

    # Parse our agent flags FIRST (strip them from args, set env) so --show-config
    # reflects the agent the same invocation would launch. Previously --show-config
    # short-circuited before this and always reported agent_spec=None even with
    # -a/--demo (gh #-dogfood).
    agent_spec, demo, args = extract_agent_args(args)
    if demo and agent_spec:
        print("ERROR: --demo and -a/--agent are mutually exclusive")
        sys.exit(1)
    if demo:
        agent_spec = DEMO_AGENT_SPEC
    if agent_spec:
        # The sidebar extension resolves LANGSTAGE_AGENT_SPEC (env beats the
        # built-in default; langstage.toml still works when nothing is set).
        # The legacy name is set too so an older installed extension version
        # keeps working with this launcher.
        os.environ["LANGSTAGE_AGENT_SPEC"] = agent_spec
        os.environ["DEEPAGENT_AGENT_SPEC"] = agent_spec

    # --show-config: print the resolved config (value, source, env var / TOML
    # key for each) and exit — now reflecting any -a/--demo parsed above.
    if "--show-config" in args:
        from langstage_jupyter.config import LabConfig
        # Hide keys the LAUNCHER doesn't honor, so --show-config never advertises
        # an env var with a confident source that has no effect here:
        #   host/port  — JupyterLab binds localhost on the auto-detected/--port port (gh #30)
        #   title      — inherited from the web-app HostConfig; read nowhere in this stage
        #   jupyter_token / jupyter_server_url — auto-generated/-detected at startup;
        #     the launcher overrides whatever was resolved (pin via JUPYTER_TOKEN). (gh #34)
        print(
            LabConfig.resolve().describe(
                omit_keys=["host", "port", "title", "jupyter_token", "jupyter_server_url"]
            )
        )
        return

    # --verify: preflight the agent the extension WOULD run — resolve the spec the
    # same way, load it, and run ONE real turn through the shared langstage-core
    # primitive; exit 0 if it completed cleanly, non-zero otherwise. The extension's
    # /health only checks the agent object is non-None; this proves it can actually
    # complete a turn (a bad key / broken tool / bad graph fails here, not at first
    # chat). Uses --demo for a keyless check. (ADR 0004)
    if "--verify" in args:
        from langstage_core.agui import verify as _core_verify
        from langstage_jupyter.config import LabConfig

        spec = str(LabConfig.resolve().agent_spec or "").strip()
        try:
            if spec:
                from langstage_core import load_agent_spec

                graph = load_agent_spec(spec)
            else:
                # No explicit spec -> the bundled default agent (same object the
                # extension builds at import).
                from langstage_jupyter.agent import agent as graph
        except Exception as e:  # noqa: BLE001 - report a load failure cleanly
            print(f"[fail] could not load agent: {e}")
            sys.exit(1)

        result = _core_verify(graph)
        if result.ok:
            print(f"[ ok ] agent verified: {result.reason}")
            sys.exit(0)
        print(f"[fail] agent verification failed: {result.reason}")
        sys.exit(1)

    # --serve-check: the HTTP counterpart of --verify. Boot the server extension
    # headlessly and prove the DEPLOYED endpoint serves a turn — catching route/
    # registration/handler regressions --verify structurally can't (ADR 0004).
    # Defaults to the keyless demo agent (CI-safe); honors -a for a real agent.
    if "--serve-check" in args or "--smoke" in args:
        sys.exit(serve_check(agent_spec))

    if agent_spec:
        print(f"Agent spec: {agent_spec}")

    # Headline command runs `jupyter lab` — bail with a clear hint up front if
    # JupyterLab isn't installed, instead of letting the jupyter dispatcher dump
    # its help later (gh #24).
    ensure_jupyterlab()

    # Check if user specified a port
    user_port = None
    for i, arg in enumerate(args):
        if arg == '--port' and i + 1 < len(args):
            raw = args[i + 1]
        elif arg.startswith('--port='):
            raw = arg.split('=', 1)[1]
        else:
            continue
        # A --port was supplied — it MUST parse. If it doesn't (including an empty
        # value, e.g. `--port=$PORT` with PORT unset), fail fast with a clear
        # message. Previously we silently swallowed the parse error, auto-detected
        # our OWN port, AND still passed the user's malformed --port token through
        # to jupyter lab — so jupyter aborted with a confusing "port only accepts
        # one value, got 2" naming a port the user never typed. (gh #40)
        try:
            user_port = int(raw)
        except (ValueError, TypeError):
            print(f"ERROR: invalid --port value: {raw!r}")
            sys.exit(1)
        break

    # Find available port
    if user_port:
        port = user_port
        print(f"Using user-specified port: {port}")
    else:
        port = find_available_port()
        print(f"Auto-detected available port: {port}")

    # Generate token (or use existing if set)
    token = os.getenv('JUPYTER_TOKEN')
    if not token:
        token = generate_token()
        print("Generated secure authentication token")
    else:
        print("Using existing JUPYTER_TOKEN from environment")

    # Determine server URL
    # Use localhost for security (only local connections)
    server_url = f"http://localhost:{port}"

    # Set environment variables for the agent to use (canonical + legacy
    # names so an older installed extension version keeps working).
    os.environ['LANGSTAGE_JUPYTER_SERVER_URL'] = server_url
    os.environ['LANGSTAGE_JUPYTER_TOKEN'] = token
    os.environ['DEEPAGENT_JUPYTER_SERVER_URL'] = server_url
    os.environ['DEEPAGENT_JUPYTER_TOKEN'] = token

    print(f"\n{'='*60}")
    print("LangStage Jupyter Configuration:")
    print(f"  Server URL: {server_url}")
    print(f"  Token: {'*' * 20} (hidden for security)")
    print("  Environment variables set:")
    print("    - LANGSTAGE_JUPYTER_SERVER_URL")
    print("    - LANGSTAGE_JUPYTER_TOKEN")
    print(f"{'='*60}\n")

    # Launch JupyterLab via THIS interpreter (sys.executable -m jupyterlab),
    # not a bare `jupyter` resolved from PATH. The labextension + server-config
    # are installed into this environment; if another Jupyter sits earlier on
    # PATH (common on Windows with a user-site Python), `jupyter lab` boots the
    # wrong app and the chat sidebar silently never loads (gh #-dogfood).
    jupyter_args = [sys.executable, '-m', 'jupyterlab']

    # Inject our auto-detected port only when the user didn't supply a valid one
    # (a malformed --port already exited above), so we never pass two --port values.
    if user_port is None:
        jupyter_args.extend(['--port', str(port)])

    # Add token
    jupyter_args.extend(['--IdentityProvider.token', token])

    # Add any user-provided arguments
    jupyter_args.extend(args)

    # Launch Jupyter Lab
    print(f"Launching: {' '.join(jupyter_args)}\n")
    try:
        subprocess.run(jupyter_args, env=os.environ)
    except KeyboardInterrupt:
        print("\n\nShutting down DeepAgent Lab...")
        sys.exit(0)
    except FileNotFoundError:
        print("ERROR: 'jupyter' command not found. Please install JupyterLab:")
        print("  pip install jupyterlab")
        sys.exit(1)


if __name__ == '__main__':
    main()
