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
import errno
import importlib.util
import os
import sys
import socket
import secrets
import subprocess
from pathlib import Path
from typing import Optional

# Console-safe print for every verdict that interpolates text this launcher did not
# author (an agent's exception, a config value, a model reply): a character the console
# can't encode is backslash-escaped instead of raising UnicodeEncodeError on a cp1252
# console (gh #122, #126, #130, #140). Lives in langstage-core since 1.0.36, which adopted
# the local helper from #141.
from langstage_core.console import safe_print
from langstage_jupyter.exit_codes import EXIT_FAIL, EXIT_PAUSED, EXIT_USAGE, exit_code_for_outcome

# The keyless echo agent shipped with the shared core — see `--demo`.
DEMO_AGENT_SPEC = "langstage_core.demo.stub:graph"


def _package_version() -> str:
    """This package's version, the same string ``--version`` prints."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("langstage-jupyter")
    except PackageNotFoundError:  # pragma: no cover - source checkout w/o metadata
        return "0.0.0+local"


#: Where a source checkout (or an editable install) keeps the built labextension.
_PACKAGE_LABEXTENSION_DIR = Path(__file__).resolve().parent / "labextension"

#: The labextension's directory name under ``share/jupyter/labextensions``.
_LABEXTENSION_NAME = "langstage-jupyter"


def _labextension_version() -> Optional[str]:
    """Version of the JupyterLab extension JupyterLab will load, from its ``package.json``.

    The labextension is a separate JS bundle stamped with its own version at build
    time (hatch-nodejs-version, from the same ``package.json`` as the Python
    package). #82 showed the two can DRIFT — a stale reused bundle ships an old JS
    version while ``pip``/``--version`` report the new one — which is exactly why a
    machine-readable ``--show-config`` reports both, so a CI consumer can assert
    they agree.

    A wheel installs the bundle under ``<prefix>/share/jupyter/labextensions/``, not
    inside the package, so this used to find nothing on every pip install and report the
    Python version instead: the two fields could never disagree (gh #131). So it reads
    the first ``labextensions`` dir on the Jupyter data path that has the bundle (the one
    JupyterLab loads, first match wins), then the in-package copy of a source checkout.
    With no bundle anywhere it returns ``None`` (JSON ``null``), never the Python version.
    """
    import json as _json

    candidates = []
    try:
        from jupyter_core.paths import jupyter_path

        candidates = [Path(d) / _LABEXTENSION_NAME for d in jupyter_path("labextensions")]
    except Exception:  # noqa: BLE001 - jupyter_core missing/odd: fall back to the package
        pass
    candidates.append(_PACKAGE_LABEXTENSION_DIR)
    for directory in candidates:
        try:
            manifest = directory / "package.json"
            return _json.loads(manifest.read_text(encoding="utf-8"))["version"]
        except (OSError, ValueError, KeyError, TypeError):  # absent/corrupt manifest
            continue
    return None


_LAUNCHER_HELP = """\
langstage-jupyter - launch JupyterLab with the LangStage chat sidebar.

Usage:
  langstage-jupyter [launcher options] [jupyter lab options...]

Launcher options:
  -a, --agent SPEC   Agent to load (module:attr or path/to/file.py:attr).
  --demo             Use the built-in keyless demo agent (no API key).
  --show-config      Print the resolved configuration and exit.
                     Add --json to emit it as a single machine-readable JSON
                     object (value + source per key) on stdout; exit 0.
  --verify           Preflight the agent (run one real turn); exit 0/1. Then exit.
  --ask "PROMPT"     Run ONE turn against the resolved agent, print its reply to stdout,
                     and exit (0 complete / 1 error / 2 interrupted). No browser/server;
                     the terminal inner loop. Keyless via --demo. Then exit.
  --serve-check      Headless HTTP smoke test: boot the server extension, serve one
                     turn over /langstage-jupyter/chat, exit 0/1. Then exit.
  --check-connection Manual-config preflight: verify LANGSTAGE_JUPYTER_SERVER_URL +
                     LANGSTAGE_JUPYTER_TOKEN reach a running Jupyter; exit 0/1. Then exit.
  --version, -V      Print the langstage-jupyter version and exit.
  -h, --help         Show this message and exit.

All other options are passed through to `jupyter lab`
(run `jupyter lab --help` to see those).

Exit codes: 0 ok, 1 failed (bad agent, check failed, can't start), 2 paused on a
human-in-the-loop interrupt (--ask), 64 usage error (bad or conflicting flags)."""


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


class LauncherArgError(ValueError):
    """A launcher-only flag was given without the value it needs (gh #115)."""


def _flag_value(args, i, flag, what):
    """The value after a space-form ``flag`` at ``args[i]``, or :class:`LauncherArgError`.

    A missing value (the flag is last) or one that is itself a flag (``--ask --demo``,
    typically an unset ``"$MSG"``) is an error. Those used to leak the flag into the
    ``jupyter lab`` passthrough, or quietly consume the next flag as the value (gh #115).
    The ``--flag=VALUE`` form is never checked, so a value that starts with ``-`` can
    still be passed that way.
    """
    if i + 1 >= len(args) or args[i + 1].startswith("-"):
        raise LauncherArgError(
            f"{flag} needs {what}. Pass it as `{flag} VALUE`, or `{flag}=VALUE` when the "
            "value itself starts with '-'."
        )
    return args[i + 1]


def extract_agent_args(args):
    """Split our agent flags out of the passthrough jupyter-lab args.

    Handles ``-a SPEC`` / ``--agent SPEC`` / ``-a=SPEC`` / ``--agent=SPEC`` and
    ``--demo``. Returns ``(agent_spec, demo, remaining_args)``; remaining_args go to
    ``jupyter lab`` untouched. A space-form flag with no spec after it raises
    :class:`LauncherArgError` (gh #115). ``-a=SPEC`` used to leak through to jupyter
    and the default agent ran instead (gh #135).
    """
    agent_spec = None
    demo = False
    remaining = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-a", "--agent"):
            agent_spec = _flag_value(args, i, "-a/--agent", "an agent spec (module:attr)")
            i += 2
            continue
        if arg.startswith(("--agent=", "-a=")):
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


#: Where a port is bind-tested. JupyterLab serves ``localhost`` -- 127.0.0.1 and ::1 --
#: and Windows lets the wildcard address bind alongside a specific-address listener,
#: so a wildcard-only probe called a port that another session held "free" and the
#: second launch then died on ``port_retries=0`` (gh #165). A port is free only if
#: every one of these binds.
_PROBE_ADDRESSES = (
    (socket.AF_INET, ""),
    (socket.AF_INET, "127.0.0.1"),
    (socket.AF_INET6, "::1"),
)

#: bind() errors meaning "this address doesn't exist here" (no IPv6 loopback), not "busy".
_ADDRESS_ABSENT_ERRNOS = {
    getattr(errno, name) for name in ("EADDRNOTAVAIL", "EAFNOSUPPORT", "WSAEADDRNOTAVAIL",
                                      "WSAEAFNOSUPPORT") if hasattr(errno, name)
}


def _port_is_free(port):
    """True when ``port`` binds on every address a local JupyterLab would listen on."""
    for family, host in _PROBE_ADDRESSES:
        if family == socket.AF_INET6 and not socket.has_ipv6:
            continue
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.bind((host, port))
        except OSError as e:
            if family == socket.AF_INET6 and e.errno in _ADDRESS_ABSENT_ERRNOS:
                continue  # no IPv6 loopback on this machine: nothing can hold it
            return False
    return True


def find_available_port(start_port=8888, max_attempts=None):
    """Find an available port starting from start_port.

    Scans ``max_attempts`` consecutive ports — 100 by default (8888-8987), so you
    can run ~100 concurrent sessions before auto-detection gives up. Raise or lower
    it with ``LANGSTAGE_JUPYTER_PORT_ATTEMPTS``, or pin a port with ``--port``.
    """
    if max_attempts is None:
        max_attempts = _port_attempts()
    for port in range(start_port, start_port + max_attempts):
        if _port_is_free(port):
            return port
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


def _redact_token_args(args):
    """Return a display-only copy of ``args`` with any jupyter-lab token VALUE masked.

    The startup banner promises the token is ``(hidden for security)``, but the
    ``Launching: ...`` line printed the full ``--IdentityProvider.token=<value>`` it
    hands to the subprocess in cleartext — so the secret still leaked to stdout, and
    thus to redirected/``nohup`` logs, CI job output, systemd journals, terminal
    scrollback, and screen-shares, right after the banner claimed it was hidden
    (gh #109). This masks the token in the PRINTED command only; the real value still
    rides through to ``subprocess`` unchanged, so ``ps`` visibility is unchanged — the
    launcher just stops writing the secret to its own logged stdout. Masks the same
    ``****<last4>`` fingerprint ``--show-config`` uses (gh #105), covering both accepted
    token args (``--IdentityProvider.token`` / ``--ServerApp.token``) in both the
    equals form (``--opt=value``) and the space form (``--opt value``).
    """
    from langstage_jupyter.config import _mask_secret

    redacted = list(args)
    i = 0
    while i < len(redacted):
        arg = redacted[i]
        matched = False
        for name in TOKEN_ARG_NAMES:
            if arg == name and i + 1 < len(redacted):
                redacted[i + 1] = _mask_secret(redacted[i + 1])
                i += 2
                matched = True
                break
            if arg.startswith(name + "="):
                redacted[i] = f"{name}={_mask_secret(arg.split('=', 1)[1])}"
                break
        if not matched:
            i += 1
    return redacted


# ── --serve-check: headless HTTP smoke test of the deployed extension ──
#
# The served route prefix the extension registers (handlers.setup_handlers).
SERVE_CHECK_ROUTE = "langstage-jupyter"


def _summarize_sse(lines):
    """Reduce a ``/chat`` SSE stream to ``(chunk_count, saw_complete, error, saw_interrupt)``.

    Pure so it is unit-testable without booting a server. ``lines`` is any
    iterable of raw SSE lines; only ``data: {json}`` lines carry frames. A frame
    with a non-empty ``chunk`` counts; ``{"status": "complete"}`` ends it cleanly;
    ``{"status": "error"}`` (or an ``error`` key) captures the failure message; and
    ``{"status": "interrupt"}`` is a valid human-in-the-loop pause — a turn that
    reached a well-formed interrupt streamed zero content chunks but is HEALTHY, so
    ``serve_check`` must not read it as an incomplete turn (gh #95).
    """
    import json as _json

    chunk_count = 0
    saw_complete = False
    saw_interrupt = False
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
        if frame.get("status") == "interrupt" or frame.get("interrupt"):
            saw_interrupt = True
        if frame.get("status") == "error" or frame.get("error"):
            error = frame.get("error") or frame.get("message") or "agent error"
    return chunk_count, saw_complete, error, saw_interrupt


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
                safe_print(f"[fail] serve-check: jupyter server exited before it was ready "
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
            chunks, complete, error, saw_interrupt = _summarize_sse(iter(resp))
        except urllib.error.HTTPError as e:
            safe_print(f"[fail] serve-check: POST /{SERVE_CHECK_ROUTE}/chat returned HTTP {e.code} "
                  f"({e.reason})")
            return 1
        except (urllib.error.URLError, OSError) as e:
            safe_print(f"[fail] serve-check: POST /{SERVE_CHECK_ROUTE}/chat failed: {e}")
            return 1

        if error is not None:
            safe_print(f"[fail] serve-check: the served turn errored: {error}")
            return 1

        name = health.get("agent_name") or spec
        # A human-in-the-loop agent's first turn pauses on a well-formed interrupt: it
        # streams zero content chunks but ends cleanly with {"status": "complete"} and no
        # error. That's the advertised HITL feature working — a HEALTHY served turn — so
        # it must be a distinct [ ok ] verdict, not the "incomplete turn (streamed 0
        # chunk(s), complete=True)" false [fail] the chunks<1 gate used to give (gh #95).
        if saw_interrupt and complete:
            safe_print(f"[ ok ] served turn paused on interrupt (HITL agent) — endpoint healthy: "
                  f"agent={name!r} (routes under /{SERVE_CHECK_ROUTE}/)")
            return 0
        if chunks < 1 or not complete:
            print(f"[fail] serve-check: incomplete turn "
                  f"(streamed {chunks} chunk(s), complete={complete})")
            return 1

        safe_print(f"[ ok ] served turn verified: agent={name!r}, streamed {chunks} chunks, "
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

    # A URL without an http(s) scheme ('localhost', '8888', '//host:8888') made urllib
    # raise a bare ValueError out of main() instead of this preflight's [fail] (gh #149).
    # 'localhost:8888' parses as scheme 'localhost', so check the scheme, not just its
    # presence.
    import urllib.parse

    parsed = urllib.parse.urlsplit(server_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        safe_print(
            f"[fail] LANGSTAGE_JUPYTER_SERVER_URL={server_url!r} is not an http(s) URL. "
            "Include the scheme and host, e.g. http://localhost:8888."
        )
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


# ── --ask: headless one-shot chat that prints the agent's ACTUAL reply (gh #101) ──
#
# --verify / --serve-check / --check-connection all prove the *plumbing* is healthy but
# never show what the agent actually SAID. --ask closes the inner dev loop from the
# terminal — change agent -> see the reply — with no browser, no persistent server, no
# token juggling. The behavior twin of --verify: --verify proves the agent RUNS (its
# output discarded); --ask runs the user's OWN prompt and prints the reply.

#: The family exit codes for a one-shot turn (langstage-core ADR 0007, matches
#: langstage-agui --message): a clean turn is 0, an agent error 1, a human-in-the-loop
#: interrupt 2.
def _ask_exit_code(outcome: str) -> int:
    return exit_code_for_outcome(outcome)


def ask(prompt, *, thread_id="ask", turn_timeout=120.0):
    """Run ONE turn against the configured agent and print its reply; return an exit code.

    Resolves the agent the SAME way ``--verify`` and the sidebar runtime do — an explicit
    ``agent_spec`` (``-a`` / ``--demo`` / ``LANGSTAGE_AGENT_SPEC``), else the documented
    ``LANGSTAGE_AGENT_MODULE`` (+ ``LANGSTAGE_AGENT_VARIABLE``), else the bundled default —
    via ``AgentWrapper``'s own resolver, so ``--ask`` can't preflight a different agent than
    the one the sidebar runs (the gh #90 lesson). Then it runs one turn through the shipped
    core one-shot primitive over the **chunk wire the sidebar's ``/chat`` serves**
    (``collect_chunk_frames`` over ``iter_chunk_frames``), so the reply printed here is the
    reply the sidebar would show.

    The reply text goes to **stdout** (clean and pipe-friendly, like ``--show-config
    --json``); every status/verdict line goes to **stderr**, so ``--ask ... | grep`` sees
    only the agent's words. Exit code mirrors the turn outcome (complete=0 / error=1 /
    interrupted=2), the same contract as the other preflights and ``langstage-agui
    --message``. Keyless via ``--demo`` (the echo stub), so it runs in CI with no API key.
    """
    import asyncio
    import contextlib

    from langstage_core.agui.collect import collect_chunk_frames
    from langstage_jupyter import config, handlers
    from langstage_jupyter.agent_wrapper import AgentWrapper
    from langstage_jupyter.config import LabConfig

    loaded_spec = None
    result = None
    # Keep stdout pure for the agent's reply (pipe-friendly — `--ask ... | grep`, like
    # --show-config --json separates its streams). The shared resolver/loader print progress
    # ("Using agent: ...") to stdout, and a user agent may debug-print
    # mid-turn; fold ALL of that into stderr for the resolve+load+turn, then print ONLY the
    # reply to the real stdout below. An early-return failure exits inside this block with
    # stdout already restored by the context manager.
    with contextlib.redirect_stdout(sys.stderr):
        cfg = LabConfig.resolve()

        # Resolve which agent to load off the live cfg (env / langstage.toml, canonical-wins)
        # — the SAME resolver AgentWrapper.__init__ and --verify use, so all three agree.
        # A malformed spec (from env / langstage.toml) is a clean [fail], never a silent
        # fallback to the default agent (gh #151).
        try:
            module, variable = AgentWrapper.resolve_agent_target(
                cfg.agent_spec, cfg.agent_module, cfg.agent_variable
            )
        except ValueError as e:
            safe_print(f"[fail] could not load agent: {e}", file=sys.stderr)
            return 1

        # Same cheap credential preflight --verify runs, scoped to the BUNDLED DEFAULT agent —
        # the only one whose model (and thus required key) we know. A custom/BYO agent is the
        # operator's concern. Gives the missing-key case a clean actionable verdict on stderr
        # instead of a raw provider TypeError mid-turn (gh #60/#90, matching /health via
        # config.is_bundled_default so they can't drift).
        if config.runs_bundled_default(cfg):  # incl. the default selected by name (gh #112)
            missing = handlers._missing_provider_key(str(cfg.model_name or "").strip())
            if missing:
                print(
                    f"[fail] cannot ask: {missing} is not set — the default agent's turn "
                    "would fail. Set it, pass --demo for the keyless agent, or select your "
                    "own with -a.",
                    file=sys.stderr,
                )
                return 1

        _apply_agent_workspace(cfg)  # before the import, like the sidebar runtime (gh #148)

        # Build the SAME agent object the sidebar runs, through AgentWrapper's own loader
        # (strict module:variable spec, same implicit agent->graph fallback). A load failure is
        # a clean [fail], never an uncaught traceback (gh #92, mirroring --verify).
        try:
            graph, loaded_spec = AgentWrapper.load_agent_from_target(module, variable)
        except Exception as e:  # noqa: BLE001 - report a load failure cleanly
            safe_print(f"[fail] could not load agent: {e}", file=sys.stderr)
            return 1

        # Run one turn through the shipped core one-shot primitive. collect_chunk_frames builds
        # the AG-UI agent (attaching a checkpointer) and drives iter_chunk_frames to a typed
        # TurnResult — the same wire the sidebar streams, so the printed reply matches. Guard
        # the whole call so a non-runnable graph (wrong type / uncompiled) is a clean [fail],
        # not a crash (gh #92).
        try:
            result = asyncio.run(
                asyncio.wait_for(
                    collect_chunk_frames(graph, prompt, thread_id), timeout=turn_timeout
                )
            )
        except asyncio.TimeoutError:
            print(f"[fail] agent turn timed out after {turn_timeout:g}s", file=sys.stderr)
            return 1
        except Exception as e:  # noqa: BLE001 - any turn failure is a clean [fail]
            safe_print(f"[fail] agent turn failed: {e}", file=sys.stderr)
            return 1

    # The reply text to stdout (may be empty for a tool-only / interrupted turn); the
    # verdict to stderr. Exit code from the outcome.
    if result.text:
        safe_print(result.text)
    if result.outcome == "error":
        safe_print(f"[fail] agent errored: {result.error}", file=sys.stderr)
    elif result.outcome == "interrupted":
        print(
            "[note] agent paused on an interrupt (human-in-the-loop) — no final reply yet",
            file=sys.stderr,
        )
    else:
        safe_print(f"[ ok ] one turn completed cleanly (agent {loaded_spec!r})", file=sys.stderr)
    return _ask_exit_code(result.outcome)


def _apply_agent_workspace(cfg):
    """Publish the agent's workspace root before a preflight imports the agent (gh #148).

    The same root the sidebar runtime applies before its first load: the pinned
    ``workspace_root`` if one is set, else the directory ``jupyter lab`` would serve (this
    process's cwd). A custom agent that reads ``LANGSTAGE_WORKSPACE_ROOT`` at import, as the
    README shows, then sees the same value under ``--verify`` / ``--ask`` as in the sidebar.
    """
    from langstage_core import apply_workspace

    pinned = cfg.sources.get("workspace_root", "default") != "default"
    apply_workspace(cfg.workspace_root if pinned else os.getcwd())


def _extract_ask(args):
    """Pull ``--ask PROMPT`` / ``--ask=PROMPT`` out of args; return ``(prompt, remaining)``.

    Mirrors ``_find_user_token``'s space-and-equals scan. ``--ask`` is a launcher flag, not
    a ``jupyter lab`` one, so it's stripped from the passthrough. Returns ``prompt=None``
    when absent (feature off); an explicitly empty ``--ask=`` / ``--ask ""`` is a real (if
    odd) prompt and is returned as ``""`` so the caller can still run a turn with it. A
    bare ``--ask``, or one followed by another flag, raises :class:`LauncherArgError`
    instead of booting a server or eating that flag as the prompt (gh #115).
    """
    prompt = None
    remaining = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--ask":
            prompt = _flag_value(args, i, "--ask", "a prompt")
            i += 2
            continue
        if arg.startswith("--ask="):
            prompt = arg.split("=", 1)[1]
            i += 1
            continue
        remaining.append(arg)
        i += 1
    return prompt, remaining


#: The spellings `jupyter lab` accepts for the server port. ``--port`` is an alias for
#: ``--ServerApp.port``; missing the long one made the launcher inject a second port and
#: jupyter abort with "port only accepts one value, got 2" (gh #138).
PORT_ARG_NAMES = ("--port", "--ServerApp.port")

#: ``port_retries`` spellings. Jupyter's default (50) moves a busy port to the next free
#: one, so the URL the launcher published would point at the wrong server (gh #129).
PORT_RETRIES_ARG_NAMES = ("--port-retries", "--ServerApp.port_retries")


def _find_arg_value(args, names):
    """The raw value of the first ``NAME VALUE`` / ``NAME=VALUE`` for any of ``names``,
    else ``None``. The same space-and-equals scan as :func:`_find_user_token`."""
    for i, arg in enumerate(args):
        for name in names:
            if arg == name and i + 1 < len(args):
                return args[i + 1]
            if arg.startswith(name + "="):
                return arg.split("=", 1)[1]
    return None


#: The spellings `jupyter lab` accepts for its serving root (gh #150).
ROOT_DIR_ARG_NAMES = ("--notebook-dir", "--ServerApp.root_dir", "--ServerApp.notebook_dir")


def _pinned_workspace_root() -> Optional[Path]:
    """The pinned workspace root the launched server will use, or ``None`` if unpinned.

    Resolved like the server resolves it: env, ``langstage.toml``, and the launch dir's
    ``.env``, which the server extension loads (without overriding real env vars) before
    reading its config. Reading only os.environ here would miss a ``.env`` pin that the
    server then honors, and split the roots again (gh #150).
    """
    from dotenv import dotenv_values, find_dotenv

    from langstage_jupyter.config import LabConfig

    dotenv_path = find_dotenv(usecwd=True)
    from_dotenv = {}
    if dotenv_path:
        from_dotenv = {k: v for k, v in dotenv_values(dotenv_path).items() if v is not None}
    cfg = LabConfig.resolve(env={**from_dotenv, **os.environ})
    if cfg.sources.get("workspace_root", "default") == "default":
        return None
    return Path(cfg.workspace_root).expanduser().resolve()


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
        print(f"langstage-jupyter {_package_version()}")
        return

    # Parse our agent flags FIRST (strip them from args, set env) so --show-config
    # reflects the agent the same invocation would launch. Previously --show-config
    # short-circuited before this and always reported agent_spec=None even with
    # -a/--demo (gh #-dogfood).
    # Strip our one-shot --ask PROMPT too (it's a launcher flag, not a jupyter one), so it
    # never leaks into the `jupyter lab` passthrough. Parsed here so --ask reflects the same
    # -a/--demo agent this invocation would launch. (gh #101)
    try:
        agent_spec, demo, args = extract_agent_args(args)
        ask_prompt, args = _extract_ask(args)
    except LauncherArgError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(EXIT_USAGE)  # a usage error (ADR 0007); was 1
    if demo and agent_spec:
        print("ERROR: --demo and -a/--agent are mutually exclusive", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    if demo:
        agent_spec = DEMO_AGENT_SPEC
    # Which launcher flag chose the agent, for --show-config's source column (gh #121).
    agent_flag = "--demo" if demo else ("-a/--agent" if agent_spec else None)
    if agent_spec:
        # Reject a malformed -a up front (colon-less 'my_agent.py', empty object name) with
        # core's own message, so --verify / --ask / --serve-check / the launch fail cleanly
        # instead of silently running the default agent (gh #151).
        from langstage_core.host import parse_agent_spec

        try:
            parse_agent_spec(agent_spec)
        except ValueError as e:
            safe_print(f"ERROR: -a/--agent: {e}", file=sys.stderr)
            sys.exit(EXIT_USAGE)  # a malformed flag value is a usage error (ADR 0007)

    # --show-config: print the resolved config (value, source, env var / TOML
    # key for each) and exit — now reflecting any -a/--demo parsed above.
    if "--show-config" in args:
        from langstage_jupyter.config import LabConfig
        # -a / --demo is a CLI override, credited to the flag. It used to be written into
        # os.environ first, so --show-config (and --json) reported the agent as coming from
        # env:LANGSTAGE_AGENT_SPEC, a variable the user never set (gh #121).
        cfg = LabConfig.resolve(overrides={"agent_spec": agent_spec})
        if agent_flag:
            cfg.sources["agent_spec"] = f"cli:{agent_flag}"
        # Hide keys the LAUNCHER doesn't honor, so --show-config never advertises
        # an env var with a confident source that has no effect here:
        #   host/port  — JupyterLab binds localhost on the auto-detected/--port port (gh #30)
        #   title      — inherited from the web-app HostConfig; read nowhere in this stage
        omit = ["host", "port", "title"]
        # jupyter_token / jupyter_server_url are auto-detected/minted by the LAUNCHER,
        # so hiding them is right for the LAUNCHER flow (they'd advertise a confident
        # source the launcher overrides — gh #34). But the documented MANUAL-config flow
        # (LANGSTAGE_JUPYTER_SERVER_URL / _TOKEN, or jupyter.server_url / jupyter.token in
        # langstage.toml) has the USER set these, LabConfig resolves them, and
        # --check-connection honors them — so when their resolved source is NOT `default`,
        # SHOW them (the token masked by config.py) so that flow is actually verifiable
        # and --check-connection's "Check LANGSTAGE_JUPYTER_SERVER_URL" points somewhere
        # --show-config can confirm. (gh #105)
        for key in ("jupyter_token", "jupyter_server_url"):
            if cfg.sources.get(key, "default") == "default":
                omit.append(key)
        # --json: emit the SAME resolved config + provenance as a single machine-readable
        # object so a CI/tooling consumer can assert on which layer won for a key without
        # regexing the human table's [source] bracket (gh #88). Exit 0; everything else
        # (e.g. the malformed-TOML note core prints on resolve) stays on stderr so stdout
        # is pure JSON, pipe-friendly: `... --show-config --json | jq .config.model_name`.
        if "--json" in args:
            import json
            # Same omit-list as the human table above → identical key set + source labels
            # (config_dict pins that it agrees with describe()). langstage-core's `toml`
            # block reports a found-but-unparseable file as `malformed` (gh #86).
            data = cfg.config_dict(omit_keys=omit)
            payload = {
                "version": _package_version(),
                "labextension_version": _labextension_version(),
                "config": data["config"],
                "toml": data["toml"],
            }
            # default=str renders non-JSON-native resolved values (e.g. a Path
            # workspace_root) as the same string the human table shows.
            print(json.dumps(payload, indent=2, default=str))
            return
        safe_print(cfg.describe(omit_keys=omit))
        return

    if agent_spec:
        # Every other path hands the agent to code that reads the config (the sidebar
        # extension in the jupyter subprocess, --verify/--ask/--serve-check here), so publish
        # it as LANGSTAGE_AGENT_SPEC (env beats the built-in default; langstage.toml still
        # works when nothing is set). The legacy name is set too so an older installed
        # extension version keeps working with this launcher.
        os.environ["LANGSTAGE_AGENT_SPEC"] = agent_spec
        os.environ["DEEPAGENT_AGENT_SPEC"] = agent_spec

    # --verify: preflight the agent the extension WOULD run — resolve the spec the
    # same way, load it, and run ONE real turn through the shared langstage-core
    # primitive; exit 0 if it completed cleanly, non-zero otherwise. The extension's
    # /health only checks the agent object is non-None; this proves it can actually
    # complete a turn (a bad key / broken tool / bad graph fails here, not at first
    # chat). Uses --demo for a keyless check. (ADR 0004)
    if "--verify" in args:
        from langstage_core.agui import verify as _core_verify
        from langstage_jupyter import config
        from langstage_jupyter.agent_wrapper import AgentWrapper
        from langstage_jupyter.config import LabConfig

        cfg = LabConfig.resolve()

        # Resolve the agent the SAME way the sidebar runtime (AgentWrapper) does —
        # agent_spec, else agent_module (+ agent_variable), else the bundled default — by
        # delegating to AgentWrapper's own resolver. Previously --verify keyed off
        # agent_spec ALONE and silently preflighted the bundled default whenever the agent
        # was selected via the documented LANGSTAGE_AGENT_MODULE + LANGSTAGE_AGENT_VARIABLE
        # vars, so --verify checked a DIFFERENT agent than the sidebar actually runs (gh
        # #90). Drive the shared resolver off the live cfg (env / langstage.toml,
        # canonical-wins), which in a real launch matches the frozen config.* constants
        # AgentWrapper reads.
        try:
            module, variable = AgentWrapper.resolve_agent_target(
                cfg.agent_spec, cfg.agent_module, cfg.agent_variable
            )
        except ValueError as e:  # malformed spec: a clean [fail], never a fallback (gh #151)
            safe_print(f"[fail] could not load agent: {e}")
            sys.exit(1)

        # The cheap credential preflight is scoped to the BUNDLED DEFAULT agent — the one
        # whose model spec (and thus required key) we know (gh #60/#66, matching /health).
        # The default is in play only when the user configured NO agent at all: no spec AND
        # module/variable both unset. A custom/BYO agent selected via spec OR
        # module+variable is the operator's concern and gets the full one-real-turn check
        # below. Keying this off `not spec` alone is exactly what made --verify demand
        # ANTHROPIC_API_KEY for a keyless module+variable agent that never needed it — for
        # "the default agent" the user never configured (gh #90). The predicate lives in
        # config.is_bundled_default so --verify and /health can't drift (gh #94).
        # runs_bundled_default also covers the default selected BY NAME
        # (LANGSTAGE_AGENT_SPEC=langstage_jupyter.agent:agent, as .env.example suggests),
        # which used to skip this and hit a raw provider TypeError (gh #112).
        if config.runs_bundled_default(cfg):
            from langstage_jupyter import handlers

            missing = handlers._missing_provider_key(str(cfg.model_name or "").strip())
            if missing:
                print(
                    f"[fail] agent verification failed: {missing} is not set — the default "
                    "agent's first turn would fail. Set it and re-run."
                )
                sys.exit(1)

        # Publish the workspace root before the import, as the sidebar runtime does, so an
        # agent reading LANGSTAGE_WORKSPACE_ROOT at import sees it here too (gh #148).
        _apply_agent_workspace(cfg)

        # Build the SAME agent object the sidebar runs, through AgentWrapper's own loader
        # (same strict module:variable spec, same implicit agent→graph fallback), so the
        # preflight and the runtime can't build different graphs (gh #90).
        try:
            graph, _loaded_spec = AgentWrapper.load_agent_from_target(module, variable)
        except Exception as e:  # noqa: BLE001 - report a load failure cleanly
            safe_print(f"[fail] could not load agent: {e}")
            sys.exit(1)

        # gh #92: the load succeeds for a non-runnable export — a wrong-TYPE object (a dict,
        # None, a function) or an uncompiled StateGraph — but building/running that object can
        # still raise, and an unguarded `_core_verify(graph)` let that escape as a raw 30-line
        # traceback (wrong-type) or a leaked internal AttributeError string (uncompiled). Guard
        # the verify call the same way the load above is guarded so any non-runnable export is a
        # clean `[fail]` verdict + exit 1, never an uncaught crash. core >=1.0.30 already turns
        # both documented cases into an `ok=False` verdict with actionable guidance ("...got
        # dict", "call .compile()"); this try/except is the belt-and-suspenders safety net at the
        # same external-call boundary, so a future/edge case that raises here can't crash --verify.
        try:
            result = _core_verify(graph)
        except Exception as e:  # noqa: BLE001 - report a verify failure cleanly
            safe_print(f"[fail] could not verify agent: {e}")
            sys.exit(1)
        if result.ok:
            safe_print(f"[ ok ] agent verified: {result.reason}")
            sys.exit(0)
        safe_print(f"[fail] agent verification failed: {result.reason}")
        sys.exit(1)

    # --serve-check: the HTTP counterpart of --verify. Boot the server extension
    # headlessly and prove the DEPLOYED endpoint serves a turn — catching route/
    # registration/handler regressions --verify structurally can't (ADR 0004).
    if "--serve-check" in args or "--smoke" in args:
        from langstage_jupyter import config
        from langstage_jupyter.agent_wrapper import AgentWrapper
        from langstage_jupyter.config import LabConfig

        # Resolve the agent the SAME way --verify and the sidebar runtime (AgentWrapper)
        # do — honoring the documented LANGSTAGE_AGENT_SPEC (and AGENT_MODULE/_VARIABLE),
        # not just the CLI -a — so the HTTP smoke test boots the agent that will actually
        # serve. Previously --serve-check passed the CLI agent_spec ALONE and silently
        # smoke-tested the bundled keyless demo whenever the agent was selected via the
        # documented env config, reporting a green [ ok ] for the WRONG agent — a
        # false-GREEN preflight (gh #110, the --serve-check twin of #90's --verify fix).
        # Fall back to the keyless demo (CI-safe) ONLY when nothing is configured at all.
        cfg = LabConfig.resolve()
        if config.is_bundled_default(cfg):
            resolved_spec = None
        else:
            try:
                module, variable = AgentWrapper.resolve_agent_target(
                    cfg.agent_spec, cfg.agent_module, cfg.agent_variable
                )
            except ValueError as e:  # malformed spec: a clean [fail] (gh #151)
                safe_print(f"[fail] serve-check: could not load agent: {e}")
                sys.exit(1)
            resolved_spec = f"{module}:{variable}" if variable else module
        sys.exit(serve_check(resolved_spec))

    # --check-connection: the MANUAL-config connection preflight (gh #67). Confirm the
    # configured LANGSTAGE_JUPYTER_SERVER_URL + LANGSTAGE_JUPYTER_TOKEN actually reach a
    # running, auth-matching Jupyter — the one "must match" invariant with no verifier
    # (--verify never opens HTTP; --serve-check boots its own server with a fresh token).
    if "--check-connection" in args or "--check-server" in args:
        sys.exit(check_connection())

    # --ask "<prompt>": the behavior twin of --verify. --verify proves the agent RUNS (its
    # reply discarded); --ask runs the user's OWN prompt and PRINTS the reply, then exits
    # (complete=0 / error=1 / interrupted=2). Resolves + loads the agent exactly like
    # --verify (honoring -a / --demo / LANGSTAGE_AGENT_MODULE + _VARIABLE — set into the env
    # above), then runs one turn via the shipped core one-shot primitive. Closes the inner
    # dev loop from the terminal, no browser. (gh #101)
    if ask_prompt is not None:
        sys.exit(ask(ask_prompt))

    if agent_spec:
        safe_print(f"Agent spec: {agent_spec}")

    # Headline command runs `jupyter lab` — bail with a clear hint up front if
    # JupyterLab isn't installed, instead of letting the jupyter dispatcher dump
    # its help later (gh #24).
    ensure_jupyterlab()

    # Check if user specified a port, via `--port` or its `--ServerApp.port` alias (gh #138).
    # A supplied port MUST parse. If it doesn't (including an empty value, e.g.
    # `--port=$PORT` with PORT unset), fail fast with a clear message. Previously we
    # silently swallowed the parse error, auto-detected our OWN port, AND still passed the
    # user's malformed --port token through to jupyter lab — so jupyter aborted with a
    # confusing "port only accepts one value, got 2" naming a port the user never typed.
    # (gh #40) `--port 0` (let the OS pick) is refused too: the launcher publishes the URL
    # to the agent's notebook tools before the server binds, so it can't know a port the
    # OS picks later, and it used to read 0 as "no port" and advertise :8888 (gh #155).
    user_port = None
    raw = _find_arg_value(args, PORT_ARG_NAMES)
    if raw is not None:
        try:
            user_port = int(raw)
        except (ValueError, TypeError):
            user_port = None
        if user_port is None or not 1 <= user_port <= 65535:
            print(
                f"ERROR: invalid --port value: {raw!r}. Pass a port between 1 and 65535 "
                "(the agent's notebook tools are pointed at it before the server starts, "
                "so 0 = 'any free port' can't be used), or omit --port to auto-detect one."
            )
            sys.exit(EXIT_USAGE)  # a bad flag value is a usage error (ADR 0007)

    # Find available port
    if user_port is not None:
        port = user_port
        print(f"Using user-specified port: {port}")
    else:
        try:
            port = find_available_port()
        except RuntimeError as e:  # no free port: can't start -> 1, cleanly (ADR 0007)
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(EXIT_FAIL)
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
    # --ServerApp.token (respected as-is and NOT re-injected — see below), then the
    # documented LANGSTAGE_JUPYTER_TOKEN (legacy DEEPAGENT_JUPYTER_TOKEN, with core's
    # one-time deprecation notice; or jupyter.token in langstage.toml), then Jupyter's own
    # JUPYTER_TOKEN, then a freshly generated secure token. The launcher used to read only
    # JUPYTER_TOKEN, so the token --show-config advertised was discarded (gh #139).
    from langstage_jupyter.config import LabConfig

    launch_cfg = LabConfig.resolve()
    token_source = launch_cfg.sources.get("jupyter_token", "default")
    if user_token is not None:
        token = user_token
        print("Using user-specified token (--IdentityProvider.token/--ServerApp.token)")
    elif token_source != "default" and str(launch_cfg.jupyter_token or ""):
        token = str(launch_cfg.jupyter_token)
        print(f"Using the Jupyter token from {token_source}")
    elif os.getenv('JUPYTER_TOKEN'):
        token = os.environ['JUPYTER_TOKEN']
        print("Using existing JUPYTER_TOKEN from environment")
    else:
        token = generate_token()
        print("Generated secure authentication token")

    # Serve the pinned workspace (gh #150). The notebook tools work through the contents
    # API, so they always act on JupyterLab's serving root, while the agent's file tools
    # use the pinned LANGSTAGE_WORKSPACE_ROOT. Launched from anywhere else (the README's
    # "Agent Portability" recipe), the two toolsets wrote to different directories. An
    # explicit --notebook-dir / --ServerApp.root_dir wins, with a warning if it differs.
    serve_root_arg = None
    pinned_root = _pinned_workspace_root()
    if pinned_root is not None:
        user_root = _find_arg_value(args, ROOT_DIR_ARG_NAMES)
        if user_root is None:
            serve_root_arg = f"--ServerApp.root_dir={pinned_root}"
            safe_print(f"Serving the pinned workspace: {pinned_root}")
        else:
            from langstage_jupyter.config import workspace_serving_mismatch

            message = workspace_serving_mismatch(user_root, pinned_root)
            if message:
                safe_print(message)

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

    # Bind exactly the port published above, or fail. JupyterLab's port_retries (default
    # 50) otherwise moves a busy port to the next free one without an error, and the
    # agent's notebook tools keep calling the old URL: nothing there, or a different
    # server (gh #129). A port_retries the user set explicitly is left alone.
    if _find_arg_value(args, PORT_RETRIES_ARG_NAMES) is None:
        jupyter_args.append('--ServerApp.port_retries=0')

    # Inject our token only when the user didn't pin one themselves — otherwise
    # jupyter_server sees the token twice and aborts (gh #69, the token twin of #40).
    # The user's own --IdentityProvider.token/--ServerApp.token rides through in `args`.
    #
    # Emit the EQUALS form, never `['--IdentityProvider.token', token]`. `token_urlsafe`
    # draws from the base64url alphabet, so ~1.6% of generated tokens start with `-`;
    # in the space form jupyter lab's argparse reads that value as another option flag
    # and aborts before booting — `argument --IdentityProvider.token: expected one
    # argument`, no server, no sidebar, and it "works on re-run" (gh #79). `--opt=value`
    # is never re-split, so a leading `-` is safe. This also covers a leading-dash
    # JUPYTER_TOKEN from the environment, and matches --serve-check's
    # f"--ServerApp.token={token}" above.
    if user_token is None:
        jupyter_args.append(f'--IdentityProvider.token={token}')

    if serve_root_arg is not None:
        jupyter_args.append(serve_root_arg)

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

    # Launch Jupyter Lab. Mask the token in the PRINTED command so we don't
    # contradict the banner's "(hidden for security)" by leaking it in cleartext on
    # the very next line (gh #109) — the subprocess below still gets the real args.
    safe_print(f"Launching: {' '.join(_redact_token_args(jupyter_args))}\n")
    try:
        # Propagate JupyterLab's exit code — otherwise a startup failure (port in use,
        # a fatal config error, the root guard) exits the launcher 0, so `set -e`, CI
        # steps, systemd, and `langstage-jupyter && next` all think it succeeded (gh #62).
        result = subprocess.run(jupyter_args, env=os.environ)
        # ...except 2: JupyterLab's argparse exits 2 on a bad passthrough flag, and the
        # family reserves 2 for "paused on a HITL interrupt" (ADR 0007), so a failed
        # launch must never read as a pause. Map it to 1 (failure).
        rc = result.returncode
        sys.exit(EXIT_FAIL if rc == EXIT_PAUSED else rc)
    except KeyboardInterrupt:
        print("\n\nShutting down DeepAgent Lab...")
        sys.exit(0)
    except FileNotFoundError:
        print("ERROR: 'jupyter' command not found. Please install JupyterLab:")
        print("  pip install jupyterlab")
        sys.exit(1)


if __name__ == '__main__':
    main()
