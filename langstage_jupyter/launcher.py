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
  --check-connection Manual-config preflight: verify LANGSTAGE_JUPYTER_SERVER_URL +
                     LANGSTAGE_JUPYTER_TOKEN reach a running Jupyter; exit 0/1. Then exit.
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


#: How many consecutive ports auto-detection probes, starting at 8888.
#: This is effectively the cap on concurrently-running `langstage-jupyter`
#: sessions (each takes the next free port). It used to be 10, so an 11th
#: concurrent session failed outright instead of just moving up a port.
DEFAULT_PORT_ATTEMPTS = 100

#: Env var to widen (or narrow) that window.
PORT_ATTEMPTS_ENV = "LANGSTAGE_JUPYTER_PORT_ATTEMPTS"


def _port_attempts():
    """Resolved scan width. Garbage or non-positive values fall back to the default."""
    raw = os.getenv(PORT_ATTEMPTS_ENV)
    if raw:
        try:
            n = int(raw)
        except ValueError:
            return DEFAULT_PORT_ATTEMPTS
        if n > 0:
            return n
    return DEFAULT_PORT_ATTEMPTS


def find_available_port(start_port=8888, max_attempts=None):
    """Find an available port starting from start_port.

    Scans ``max_attempts`` consecutive ports — 100 by default (8888-8987), so you
    can run ~100 concurrent sessions before auto-detection gives up. Raise or lower
    it with ``LANGSTAGE_JUPYTER_PORT_ATTEMPTS``, or pin a port with ``--port``.
    """
    if max_attempts is None:
        max_attempts = _port_attempts()
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', port))
                return port
        except OSError:
            continue
    last_port = start_port + max_attempts - 1
    raise RuntimeError(
        f"Could not find an available port in {start_port}-{last_port} "
        f"({max_attempts} tried). Free one up, pass --port <PORT>, or widen the "
        f"search with {PORT_ATTEMPTS_ENV}."
    )


def generate_token():
    """Generate a secure random token for Jupyter authentication."""
    return secrets.token_urlsafe(32)


#: The token arguments `jupyter lab` accepts. ``--IdentityProvider.token`` is the
#: modern name; ``--ServerApp.token`` is the documented alias. If the user passes
#: either, the launcher must not inject its own or the two collide (gh #69).
TOKEN_ARG_NAMES = ("--IdentityProvider.token", "--ServerApp.token")


def _find_user_token(args):
    """Return the token the user pinned via a jupyter-lab token arg, else ``None``.

    Mirrors the ``--port`` scan (gh #40): recognizes both the space form
    (``--IdentityProvider.token TOK``) and the equals form
    (``--IdentityProvider.token=TOK``) for either accepted name. An explicitly
    empty value (``--IdentityProvider.token=``, i.e. auth disabled) is a real
    user choice and is returned as ``""`` — distinct from ``None`` (not supplied),
    so the launcher respects "no token" and still doesn't inject its own.
    """
    for i, arg in enumerate(args):
        for name in TOKEN_ARG_NAMES:
            if arg == name and i + 1 < len(args):
                return args[i + 1]
            if arg.startswith(name + "="):
                return arg.split("=", 1)[1]
    return None


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
            # CI runners and Docker images commonly run as root, and jupyter_server
            # refuses to boot as root without this — so serve-check died before it
            # could serve in exactly the environments it targets (gh #58). Safe here:
            # an ephemeral, token-gated, localhost-only server we spawn and tear down.
            "--ServerApp.allow_root=True",
            # Local ephemeral smoke-test server; token auth already gates it and
            # exempts XSRF, but disable the check so the POST can't 403 on it.
            "--ServerApp.disable_check_xsrf=True",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    def _server_output_tail(n=6):
        """The last few lines the (now-exited) server wrote — so the real cause
        (a bad port, a config error, the root guard) is shown, not just a code
        (gh #58: the diagnostic used to be swallowed)."""
        try:
            out = proc.stdout.read() if proc.stdout else ""
        except (ValueError, OSError):  # pragma: no cover - stream already closed
            return ""
        lines = [ln for ln in (out or "").splitlines() if ln.strip()]
        return ("\n  " + "\n  ".join(lines[-n:])) if lines else ""

    try:
        # 1. Poll health until the agent is loaded (server boot + agent import).
        deadline = time.monotonic() + boot_timeout
        health = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:  # server died before serving
                tail = _server_output_tail()
                print(f"[fail] serve-check: jupyter server exited before it was ready "
                      f"(code {proc.returncode}){' — last output:' + tail if tail else ''}")
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


# ── --check-connection: preflight the MANUAL-config Jupyter connection (gh #67) ──
#
# The one documented surface with no verifier. The README's "Alternative: Manual
# Configuration" path hands the user two values they must get exactly right and warns
# in bold that they "must match" JupyterLab's startup parameters — but nothing confirms
# they actually reach a running, auth-matching server before the first chat. --verify
# never opens an HTTP connection, and --serve-check boots its OWN server with a FRESH
# token, so neither can catch a manual-config mismatch. This does.


def _connection_verdict(server_url, *, status=None, server_version=None,
                        unreachable=False, error=None):
    """Reduce a ``/api/status`` probe to ``(exit_code, message)``.

    Pure so the verdict logic is unit-testable without a live Jupyter (mirrors
    ``_summarize_sse`` for ``--serve-check``). Exactly one outcome is supplied:

    * ``unreachable=True`` — connection refused / DNS failure / timeout,
    * ``status=<int>``     — the HTTP status ``/api/status`` returned (it is
      ``@web.authenticated``, so a wrong/missing token yields 403),
    * ``error=<str>``      — some other client-side failure.

    Names the two distinct failure modes the enhancement is about — server
    *unreachable* vs. token *rejected* — so triage is one glance, not a chat session.
    """
    if unreachable:
        return 1, (
            f"[fail] {server_url} unreachable — is a Jupyter server running there? "
            "Check LANGSTAGE_JUPYTER_SERVER_URL and that `jupyter lab` is up."
        )
    if error is not None:
        return 1, f"[fail] could not probe {server_url}: {error}"
    if status in (401, 403):
        return 1, (
            f"[fail] {server_url} returned {status} — LANGSTAGE_JUPYTER_TOKEN does not match "
            "the token JupyterLab was launched with (--IdentityProvider.token)."
        )
    if status == 200:
        suffix = f" (Jupyter Server {server_version})" if server_version else ""
        return 0, f"[ ok ] reached {server_url} — token accepted{suffix}"
    return 1, f"[fail] {server_url} returned unexpected HTTP {status}"


def check_connection(*, timeout=5.0):
    """Confirm the configured URL+token reach a running, auth-matching Jupyter (gh #67).

    Resolve ``LANGSTAGE_JUPYTER_SERVER_URL`` + ``LANGSTAGE_JUPYTER_TOKEN`` through the
    normal config chain (env / ``langstage.toml``, canonical-wins) and GET
    ``{server_url}/api/status`` with the token. ``/api/status`` is ``@web.authenticated``,
    so this actually exercises the token — the load-bearing check the "must match"
    invariant needs. Prints a one-line verdict and returns ``0``/``1``.

    Unlike ``--serve-check`` (which boots its own ephemeral server with a freshly
    generated token), this tests the user's *configured* values against an
    *already-running* server — the manual-config mismatch the other preflights can't see.
    """
    import json
    import urllib.error
    import urllib.request

    from langstage_jupyter.config import LabConfig

    cfg = LabConfig.resolve()
    server_url = str(cfg.jupyter_server_url or "").strip().rstrip("/")
    token = str(cfg.jupyter_token or "").strip()

    if not server_url:
        print("[fail] LANGSTAGE_JUPYTER_SERVER_URL is not set — nothing to check.")
        return 1

    def _get(path):
        req = urllib.request.Request(
            f"{server_url}{path}",
            headers={"Authorization": f"token {token}"} if token else {},
        )
        return urllib.request.urlopen(req, timeout=timeout)

    try:
        status = _get("/api/status").getcode()
    except urllib.error.HTTPError as e:
        code, message = _connection_verdict(server_url, status=e.code)
        print(message)
        return code
    except (urllib.error.URLError, ConnectionError, OSError):
        # Connection refused / DNS / timeout — the server isn't reachable at that URL.
        code, message = _connection_verdict(server_url, unreachable=True)
        print(message)
        return code

    # Token accepted. Best-effort: enrich the verdict with the server version from the
    # unauthenticated /api endpoint (/api/status doesn't carry it). Never fail on this.
    version = None
    try:
        version = json.loads(_get("/api").read()).get("version")
    except Exception:  # noqa: BLE001 - version is cosmetic
        pass

    code, message = _connection_verdict(server_url, status=status, server_version=version)
    print(message)
    return code


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

        cfg = LabConfig.resolve()
        spec = str(cfg.agent_spec or "").strip()

        # For the BUNDLED default agent (no explicit spec), do the same cheap credential
        # preflight /health does (gh #60) BEFORE building the agent. The model is built
        # lazily, so a missing provider key doesn't fail until the first API call — so
        # core.verify() surfaces it as a raw provider TypeError that never names the
        # variable. Name it here instead, so --verify and /health say the same thing about
        # the same failure. A custom/BYO agent's credentials stay the operator's concern
        # (matching /health scoping) and keep the full one-real-turn check below. (gh #66)
        if not spec:
            from langstage_jupyter import handlers

            missing = handlers._missing_provider_key(str(cfg.model_name or "").strip())
            if missing:
                print(
                    f"[fail] agent verification failed: {missing} is not set — the default "
                    "agent's first turn would fail. Set it and re-run."
                )
                sys.exit(1)

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

    # --check-connection: the MANUAL-config connection preflight (gh #67). Confirm the
    # configured LANGSTAGE_JUPYTER_SERVER_URL + LANGSTAGE_JUPYTER_TOKEN actually reach a
    # running, auth-matching Jupyter — the one "must match" invariant with no verifier
    # (--verify never opens HTTP; --serve-check boots its own server with a fresh token).
    if "--check-connection" in args or "--check-server" in args:
        sys.exit(check_connection())

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

    # Check if the user pinned an auth token themselves. --IdentityProvider.token
    # (and its --ServerApp.token alias) is a standard `jupyter lab` argument the
    # README advertises as supported and uses in its Manual-Config section. If the
    # user supplies it we must NOT also inject our own, or jupyter_server sees the
    # token twice and aborts with "token only accepts one value, got 2" — the token
    # twin of the #40 --port duplicate crash. Detect it, respect the user's value,
    # and wire it through to the agent below. (gh #69)
    user_token = _find_user_token(args)

    # Resolve the auth token. Precedence: a user-pinned --IdentityProvider.token /
    # --ServerApp.token (respected as-is and NOT re-injected — see below), then
    # JUPYTER_TOKEN from the env, then a freshly generated secure token.
    if user_token is not None:
        token = user_token
        print("Using user-specified token (--IdentityProvider.token/--ServerApp.token)")
    else:
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

    # Inject our token only when the user didn't pin one themselves — otherwise
    # jupyter_server sees the token twice and aborts (gh #69, the token twin of #40).
    # The user's own --IdentityProvider.token/--ServerApp.token rides through in `args`.
    if user_token is None:
        jupyter_args.extend(['--IdentityProvider.token', token])

    # Add any user-provided arguments
    jupyter_args.extend(args)

    # The jupyter/* Docker images, Binder, CI runners, K8s notebook pods, and
    # devcontainers all run as root, and jupyter_server refuses to boot as root
    # without --allow-root — so the headline `langstage-jupyter` launch died
    # immediately (exit 1, after the extension loaded) in exactly the environments
    # --serve-check was hardened for (gh #58). Mirror that treatment on the real launch
    # path (gh #64): inject --allow-root when we're root and the user hasn't already
    # passed it. Same rationale as #58 — a token-gated, localhost server.
    if (
        hasattr(os, 'geteuid')
        and os.geteuid() == 0
        and not any(a == '--allow-root' or a.startswith('--ServerApp.allow_root') for a in args)
    ):
        jupyter_args.append('--allow-root')

    # Launch Jupyter Lab
    print(f"Launching: {' '.join(jupyter_args)}\n")
    try:
        # Propagate JupyterLab's exit code — otherwise a startup failure (port in use,
        # a fatal config error, the root guard) exits the launcher 0, so `set -e`, CI
        # steps, systemd, and `langstage-jupyter && next` all think it succeeded (gh #62).
        result = subprocess.run(jupyter_args, env=os.environ)
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        print("\n\nShutting down DeepAgent Lab...")
        sys.exit(0)
    except FileNotFoundError:
        print("ERROR: 'jupyter' command not found. Please install JupyterLab:")
        print("  pip install jupyterlab")
        sys.exit(1)


if __name__ == '__main__':
    main()
