"""
Tests for launcher utilities (launcher.py).
"""
import os
import socket
import pytest
from unittest.mock import Mock, patch, MagicMock
from langstage_jupyter.launcher import (
    DEMO_AGENT_SPEC,
    _connection_verdict,
    _summarize_sse,
    check_connection,
    extract_agent_args,
    find_available_port,
    generate_token,
    main,
    serve_check,
)


class TestExtractAgentArgs:
    """Tests for the -a/--agent/--demo flag extraction."""

    def test_no_agent_flags_pass_through(self):
        spec, demo, rest = extract_agent_args(["--port", "9000", "--no-browser"])
        assert spec is None
        assert demo is False
        assert rest == ["--port", "9000", "--no-browser"]

    def test_short_flag(self):
        spec, demo, rest = extract_agent_args(["-a", "my.py:graph", "--no-browser"])
        assert spec == "my.py:graph"
        assert rest == ["--no-browser"]

    def test_long_flag_with_equals(self):
        spec, _, rest = extract_agent_args(["--agent=pkg.mod:g"])
        assert spec == "pkg.mod:g"
        assert rest == []

    def test_demo_flag(self):
        spec, demo, rest = extract_agent_args(["--demo", "--port", "9000"])
        assert spec is None
        assert demo is True
        assert rest == ["--port", "9000"]


class TestMainAgentWiring:
    """main() wires the agent flags into DEEPAGENT_AGENT_SPEC."""

    def _run_main(self, argv, monkeypatch, returncode=0):
        calls = {}

        def fake_run(cmd, env=None):
            calls["cmd"] = cmd
            calls["env_spec"] = (env or {}).get("LANGSTAGE_AGENT_SPEC")
            return MagicMock(returncode=returncode)

        monkeypatch.setattr("langstage_jupyter.launcher.subprocess.run", fake_run)
        monkeypatch.setattr("sys.argv", ["langstage-jupyter"] + argv)
        monkeypatch.delenv("LANGSTAGE_AGENT_SPEC", raising=False)
        monkeypatch.delenv("DEEPAGENT_AGENT_SPEC", raising=False)
        # main() now propagates JupyterLab's exit code via sys.exit (gh #62).
        with pytest.raises(SystemExit) as exc:
            main()
        calls["exit_code"] = exc.value.code
        return calls

    def test_demo_sets_stub_spec(self, monkeypatch):
        calls = self._run_main(["--demo", "--no-browser"], monkeypatch)
        assert calls["env_spec"] == DEMO_AGENT_SPEC
        # the flag itself never reaches jupyter lab
        assert "--demo" not in calls["cmd"]

    def test_agent_flag_sets_spec(self, monkeypatch):
        calls = self._run_main(["-a", "my.py:graph", "--no-browser"], monkeypatch)
        assert calls["env_spec"] == "my.py:graph"
        assert "-a" not in calls["cmd"]
        assert "my.py:graph" not in calls["cmd"]

    def test_launcher_propagates_jupyterlab_exit_code(self, monkeypatch):
        # gh #62: a JupyterLab startup failure (port in use, root guard, bad config)
        # exits non-zero, but the launcher discarded returncode and exited 0 — masking
        # failures from set -e / CI / systemd. It must now surface the child's code.
        calls = self._run_main(["--no-browser"], monkeypatch, returncode=1)
        assert calls["exit_code"] == 1
        # ...and a clean exit still propagates 0.
        calls = self._run_main(["--no-browser"], monkeypatch, returncode=0)
        assert calls["exit_code"] == 0

    def test_demo_and_agent_conflict(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--demo", "-a", "x.py:g"])
        with pytest.raises(SystemExit):
            main()

    def test_injects_allow_root_when_running_as_root(self, monkeypatch):
        # gh #64: the jupyter/* Docker images, Binder, CI, K8s, and devcontainers all run
        # as root, and jupyter_server refuses to boot as root without --allow-root — so
        # the headline launch died (exit 1) in exactly the environments --serve-check (#58)
        # was hardened for. main() must inject --allow-root when euid == 0.
        monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
        calls = self._run_main(["--no-browser"], monkeypatch)
        assert "--allow-root" in calls["cmd"]

    def test_no_allow_root_when_not_root(self, monkeypatch):
        # A non-root laptop must NOT get --allow-root injected (jupyter would warn/refuse
        # differently, and there's no reason to relax the guard off-root).
        monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
        calls = self._run_main(["--no-browser"], monkeypatch)
        assert "--allow-root" not in calls["cmd"]

    def test_allow_root_not_duplicated_when_user_passed_it(self, monkeypatch):
        # If the user already passed --allow-root, don't add a second one.
        monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
        calls = self._run_main(["--no-browser", "--allow-root"], monkeypatch)
        assert calls["cmd"].count("--allow-root") == 1


class TestLauncherHelpAndShowConfig:
    """--help shows the launcher's own flags; --show-config reflects -a/--demo."""

    def test_help_lists_launcher_flags(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--help"])
        main()
        out = capsys.readouterr().out
        assert "--demo" in out
        assert "--show-config" in out
        assert "--agent" in out
        assert "jupyter lab" in out  # notes the passthrough
        # ASCII-only so it doesn't mojibake on a cp1252 console.
        assert out.isascii(), "launcher help must be ASCII-safe"

    def test_show_config_reflects_agent_flag(self, monkeypatch, capsys):
        monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", "")
        monkeypatch.setenv("DEEPAGENT_AGENT_SPEC", "")
        monkeypatch.setattr(
            "sys.argv", ["langstage-jupyter", "--show-config", "-a", "foo.py:graph"]
        )
        main()
        out = capsys.readouterr().out
        assert "foo.py:graph" in out  # not agent_spec=None

    def test_help_lists_verify_flag(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--help"])
        main()
        assert "--verify" in capsys.readouterr().out

    def test_show_config_omits_inert_host_port(self, monkeypatch, capsys):
        # The launcher never honors LANGSTAGE_HOST/PORT (JupyterLab uses --port /
        # auto-detect and binds localhost), so --show-config must not advertise
        # them with a live env-var source. (gh #30)
        monkeypatch.setenv("LANGSTAGE_PORT", "7777")
        monkeypatch.setenv("LANGSTAGE_HOST", "0.0.0.0")
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--show-config"])
        main()
        out = capsys.readouterr().out
        assert "LANGSTAGE_PORT" not in out
        assert "LANGSTAGE_HOST" not in out
        assert "\n  port " not in out
        assert "\n  host " not in out
        # ...but keys this stage actually honors are still shown.
        assert "agent_spec" in out

    def test_show_config_omits_launcher_managed_keys(self, monkeypatch, capsys):
        # title is read nowhere in this stage; jupyter_token / jupyter_server_url
        # are auto-generated/-detected and the launcher overrides them — so none
        # of the three should be advertised with a live source. (gh #34)
        monkeypatch.setenv("LANGSTAGE_TITLE", "MyTitle")
        monkeypatch.setenv("LANGSTAGE_JUPYTER_TOKEN", "pinned-tok")
        monkeypatch.setenv("LANGSTAGE_JUPYTER_SERVER_URL", "http://localhost:9999")
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--show-config"])
        main()
        out = capsys.readouterr().out
        for key in ("\n  title ", "\n  jupyter_token ", "\n  jupyter_server_url "):
            assert key not in out, key
        for env in ("LANGSTAGE_TITLE", "LANGSTAGE_JUPYTER_TOKEN", "LANGSTAGE_JUPYTER_SERVER_URL"):
            assert env not in out, env
        assert "agent_spec" in out


class TestFindAvailablePort:
    """Tests for find_available_port function."""

    def test_finds_first_available_port(self):
        """Should return the first available port."""
        # The actual port returned depends on what's available
        port = find_available_port(start_port=9000, max_attempts=10)

        assert 9000 <= port < 9010
        assert isinstance(port, int)

        # Verify the port is actually available by binding to it
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', port))
            assert s.getsockname()[1] == port

    def test_skips_occupied_ports(self):
        """Should skip occupied ports and find the next available one."""
        # Occupy a port
        occupied_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        occupied_socket.bind(('', 0))  # Bind to any available port
        occupied_port = occupied_socket.getsockname()[1]

        try:
            # Find available port starting from the occupied port
            port = find_available_port(start_port=occupied_port, max_attempts=10)

            # Should return a different port
            assert port != occupied_port
            assert port >= occupied_port
        finally:
            occupied_socket.close()

    def test_raises_error_when_no_ports_available(self):
        """Should raise RuntimeError when no ports available in range."""
        # Mock socket to always raise OSError
        with patch('socket.socket') as mock_socket:
            mock_sock = MagicMock()
            mock_sock.bind.side_effect = OSError("Port in use")
            mock_sock.__enter__ = Mock(return_value=mock_sock)
            mock_sock.__exit__ = Mock(return_value=False)
            mock_socket.return_value = mock_sock

            with pytest.raises(RuntimeError, match="Could not find an available port"):
                find_available_port(start_port=8000, max_attempts=3)

    def test_default_parameters(self):
        """Should use default parameters when not specified."""
        port = find_available_port()

        # Default start_port is 8888, max_attempts is 10
        assert 8888 <= port < 8898

    def test_finds_port_in_custom_range(self):
        """Should find port in custom range."""
        port = find_available_port(start_port=7000, max_attempts=5)

        assert 7000 <= port < 7005


class TestGenerateToken:
    """Tests for generate_token function."""

    def test_generates_token(self):
        """Should generate a token string."""
        token = generate_token()

        assert isinstance(token, str)
        assert len(token) > 0

    def test_tokens_are_unique(self):
        """Should generate unique tokens."""
        tokens = [generate_token() for _ in range(10)]

        # All tokens should be unique
        assert len(tokens) == len(set(tokens))

    def test_token_is_url_safe(self):
        """Should generate URL-safe tokens."""
        token = generate_token()

        # URL-safe base64 uses only alphanumeric, hyphen, and underscore
        allowed_chars = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_')
        assert all(c in allowed_chars for c in token)

    def test_token_has_sufficient_length(self):
        """Should generate tokens with sufficient length for security."""
        token = generate_token()

        # secrets.token_urlsafe(32) generates approximately 43 characters
        assert len(token) >= 40


class TestLauncherIntegration:
    """Integration tests for launcher functionality."""

    def test_port_and_token_generation_together(self):
        """Should successfully generate both port and token."""
        port = find_available_port(start_port=9500, max_attempts=10)
        token = generate_token()

        assert isinstance(port, int)
        assert 9500 <= port < 9510
        assert isinstance(token, str)
        assert len(token) >= 40

    @patch.dict('os.environ', {}, clear=True)
    def test_environment_variable_usage(self):
        """Should use environment variables when set."""
        import os

        # Test that launcher would set these variables
        test_port = 8765
        test_token = generate_token()

        os.environ['DEEPAGENT_JUPYTER_SERVER_URL'] = f"http://localhost:{test_port}"
        os.environ['DEEPAGENT_JUPYTER_TOKEN'] = test_token

        assert os.getenv('DEEPAGENT_JUPYTER_SERVER_URL') == f"http://localhost:{test_port}"
        assert os.getenv('DEEPAGENT_JUPYTER_TOKEN') == test_token


class TestPortHandling:
    """--port parse-failure must fail fast, not inject a duplicate port (gh #40)."""

    def _run_main(self, argv, monkeypatch):
        calls = {}

        def fake_run(cmd, env=None):
            calls["cmd"] = cmd
            return MagicMock(returncode=0)

        monkeypatch.setattr("langstage_jupyter.launcher.subprocess.run", fake_run)
        monkeypatch.setattr("sys.argv", ["langstage-jupyter"] + argv)
        with pytest.raises(SystemExit):  # main() propagates the child's exit code (gh #62)
            main()
        return calls

    @pytest.mark.parametrize("bad", ["--port=", "--port=notaport"])
    def test_malformed_port_exits_cleanly(self, monkeypatch, capsys, bad):
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", bad, "--no-browser"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        assert "invalid --port value" in capsys.readouterr().out

    def test_valid_port_is_not_duplicated(self, monkeypatch):
        calls = self._run_main(["--port=19191", "--no-browser"], monkeypatch)
        # the launcher must NOT inject its own --port on top of the user's.
        assert calls["cmd"].count("--port") == 0  # space-form not added...
        assert "--port=19191" in calls["cmd"]  # ...the user's token passes through once

    def test_no_port_auto_injects_one(self, monkeypatch):
        calls = self._run_main(["--no-browser"], monkeypatch)
        assert calls["cmd"].count("--port") == 1  # exactly one, auto-detected


class TestTokenHandling:
    """A user-supplied token must not be duplicated by our injected one (gh #69),
    the untreated token twin of the #40 --port duplicate crash."""

    def _run_main(self, argv, monkeypatch):
        calls = {}

        def fake_run(cmd, env=None):
            calls["cmd"] = cmd
            calls["env"] = dict(env or {})
            return MagicMock(returncode=0)

        monkeypatch.setattr("langstage_jupyter.launcher.subprocess.run", fake_run)
        monkeypatch.setattr("sys.argv", ["langstage-jupyter"] + argv)
        monkeypatch.delenv("JUPYTER_TOKEN", raising=False)
        with pytest.raises(SystemExit):  # main() propagates the child's exit code (gh #62)
            main()
        return calls

    def _token_value_count(self, cmd):
        """How many times a token VALUE reaches jupyter lab — via either the
        space form (`--IdentityProvider.token TOK`) or the equals form
        (`--IdentityProvider.token=TOK` / `--ServerApp.token=TOK`)."""
        names = ("--IdentityProvider.token", "--ServerApp.token")
        n = 0
        for i, a in enumerate(cmd):
            if a in names and i + 1 < len(cmd):
                n += 1
            elif any(a.startswith(name + "=") for name in names):
                n += 1
        return n

    def test_no_token_arg_auto_injects_one(self, monkeypatch):
        # Baseline: with no user token, the launcher injects exactly one.
        calls = self._run_main(["--no-browser"], monkeypatch)
        assert self._token_value_count(calls["cmd"]) == 1
        assert "--IdentityProvider.token" in calls["cmd"]

    def test_user_identityprovider_token_equals_not_duplicated(self, monkeypatch):
        # gh #69: the exact repro — a pinned token via the equals form must NOT be
        # doubled, or jupyter_server aborts "token only accepts one value, got 2".
        calls = self._run_main(
            ["--no-browser", "--IdentityProvider.token=MyPinnedToken"], monkeypatch
        )
        assert self._token_value_count(calls["cmd"]) == 1  # exactly one, the user's
        assert "--IdentityProvider.token=MyPinnedToken" in calls["cmd"]
        # ...and the launcher did NOT add its own space-form token on top.
        assert "--IdentityProvider.token" not in calls["cmd"]

    def test_user_identityprovider_token_space_form_not_duplicated(self, monkeypatch):
        calls = self._run_main(
            ["--no-browser", "--IdentityProvider.token", "SpaceTok"], monkeypatch
        )
        assert self._token_value_count(calls["cmd"]) == 1
        # the user's space-form pair rides through untouched, and only once
        assert calls["cmd"].count("--IdentityProvider.token") == 1
        assert "SpaceTok" in calls["cmd"]

    def test_serverapp_token_alias_not_duplicated(self, monkeypatch):
        # The --ServerApp.token alias gets the same treatment.
        calls = self._run_main(
            ["--no-browser", "--ServerApp.token=AliasTok"], monkeypatch
        )
        assert self._token_value_count(calls["cmd"]) == 1
        assert "--IdentityProvider.token" not in calls["cmd"]  # ours not injected
        assert "--ServerApp.token=AliasTok" in calls["cmd"]

    def test_user_token_is_wired_to_the_agent_env(self, monkeypatch):
        # The agent's notebook tools authenticate with LANGSTAGE_JUPYTER_TOKEN; when
        # the user pins the server's token, that env must carry the SAME value or the
        # tools would 403 against the very server they launched.
        calls = self._run_main(
            ["--no-browser", "--IdentityProvider.token=MyPinnedToken"], monkeypatch
        )
        assert calls["env"]["LANGSTAGE_JUPYTER_TOKEN"] == "MyPinnedToken"
        assert calls["env"]["DEEPAGENT_JUPYTER_TOKEN"] == "MyPinnedToken"


class TestVerifyFlag:
    """--verify preflights the agent with one real turn via core.verify (ADR 0004)."""

    def test_verify_demo_passes_exit_zero(self, monkeypatch, capsys):
        monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", "")
        monkeypatch.setenv("DEEPAGENT_AGENT_SPEC", "")
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--demo", "--verify"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        assert "agent verified" in capsys.readouterr().out

    def test_verify_default_agent_missing_key_names_the_variable(self, monkeypatch, capsys):
        # gh #66: with the BUNDLED default agent and no ANTHROPIC_API_KEY, --verify must
        # name the missing variable — the same actionable message /health gives (gh #60) —
        # instead of dumping a raw provider `TypeError: Could not resolve authentication
        # method...`. It short-circuits BEFORE building the agent / calling core.verify.
        monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", "")  # default agent (no explicit spec)
        monkeypatch.setenv("DEEPAGENT_AGENT_SPEC", "")
        monkeypatch.delenv("LANGSTAGE_MODEL_NAME", raising=False)  # default anthropic model
        monkeypatch.delenv("DEEPAGENT_MODEL_NAME", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # If the short-circuit regressed, core.verify() would run a real turn — make that a
        # hard failure rather than a slow/networked pass.
        monkeypatch.setattr(
            "langstage_core.agui.verify",
            lambda *a, **k: pytest.fail("core.verify must not run when the default key is missing"),
        )
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--verify"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "ANTHROPIC_API_KEY" in out       # names the exact variable...
        assert "verification failed" in out     # ...as a verify verdict...
        assert "TypeError" not in out           # ...not a raw provider stack string (gh #66)

    def test_verify_default_agent_with_key_still_runs_the_real_turn(self, monkeypatch, capsys):
        # The credential short-circuit is scoped to a MISSING key: when the key is present,
        # --verify must fall through to the real one-turn check (core.verify), not skip it.
        monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", "")
        monkeypatch.setenv("DEEPAGENT_AGENT_SPEC", "")
        monkeypatch.delenv("LANGSTAGE_MODEL_NAME", raising=False)
        monkeypatch.delenv("DEEPAGENT_MODEL_NAME", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")

        # Stub the bundled default-agent module so the load path is hermetic — we're
        # asserting the credential check falls through, not building the real model.
        import sys
        import types

        fake_agent_mod = types.ModuleType("langstage_jupyter.agent")
        fake_agent_mod.agent = object()  # sentinel graph handed to core.verify
        monkeypatch.setitem(sys.modules, "langstage_jupyter.agent", fake_agent_mod)

        ran = {}

        class _Result:
            ok = True
            reason = "one turn completed cleanly"

        def fake_verify(graph, *a, **k):
            ran["graph"] = graph
            return _Result()

        monkeypatch.setattr("langstage_core.agui.verify", fake_verify)
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--verify"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        assert ran.get("graph") is fake_agent_mod.agent  # got PAST the key check to the turn
        assert "agent verified" in capsys.readouterr().out

    def test_verify_broken_agent_fails_exit_one(self, monkeypatch, capsys, tmp_path):
        agent = tmp_path / "broken.py"
        agent.write_text(
            "from langgraph.graph import StateGraph, START, END, MessagesState\n"
            "def boom(s):\n"
            "    raise RuntimeError('tool exploded')\n"
            "b = StateGraph(MessagesState)\n"
            "b.add_node('boom', boom)\n"
            "b.add_edge(START, 'boom')\n"
            "b.add_edge('boom', END)\n"
            "graph = b.compile()\n"
        )
        monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", "")
        monkeypatch.setenv("DEEPAGENT_AGENT_SPEC", "")
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "-a", f"{agent}:graph", "--verify"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        assert "verification failed" in capsys.readouterr().out


class TestSummarizeSSE:
    """_summarize_sse reduces a /chat SSE stream to (chunks, complete, error) — the
    verdict logic behind --serve-check, unit-tested without a server (gh #56)."""

    def test_clean_stream_counts_chunks_and_completion(self):
        lines = [
            'data: {"status": "streaming", "chunk": "hel"}',
            'data: {"status": "streaming", "chunk": "lo"}',
            "",  # SSE blank separators are ignored
            'data: {"status": "complete"}',
        ]
        chunks, complete, error = _summarize_sse(lines)
        assert chunks == 2
        assert complete is True
        assert error is None

    def test_bytes_lines_are_decoded(self):
        # urllib streams bytes; the helper must handle them (the real path).
        lines = [b'data: {"status": "streaming", "chunk": "hi"}', b'data: {"status": "complete"}']
        chunks, complete, error = _summarize_sse(lines)
        assert chunks == 1 and complete is True and error is None

    def test_error_frame_is_captured(self):
        lines = ['data: {"status": "error", "error": "kaboom"}']
        chunks, complete, error = _summarize_sse(lines)
        assert error == "kaboom"
        assert complete is False

    def test_incomplete_stream_has_no_completion(self):
        lines = ['data: {"status": "streaming", "chunk": "x"}']  # no complete frame
        chunks, complete, error = _summarize_sse(lines)
        assert chunks == 1 and complete is False and error is None

    def test_non_data_and_malformed_lines_ignored(self):
        lines = ["event: ping", "data: not json", 'data: {"status": "complete"}']
        chunks, complete, error = _summarize_sse(lines)
        assert chunks == 0 and complete is True and error is None


class TestServeCheckRouting:
    """main() routes --serve-check / --smoke to serve_check() and exits with its code."""

    @pytest.mark.parametrize("flag", ["--serve-check", "--smoke"])
    def test_flag_calls_serve_check_and_exits_with_its_code(self, flag, monkeypatch):
        called = {}

        def fake_serve_check(spec=None, **kw):
            called["spec"] = spec
            return 0

        monkeypatch.setattr("langstage_jupyter.launcher.serve_check", fake_serve_check)
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", flag])
        monkeypatch.delenv("LANGSTAGE_AGENT_SPEC", raising=False)
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        assert "spec" in called  # it was actually routed here (not to jupyter lab)

    def test_serve_check_honors_explicit_agent_flag(self, monkeypatch):
        called = {}
        monkeypatch.setattr(
            "langstage_jupyter.launcher.serve_check",
            lambda spec=None, **kw: called.setdefault("spec", spec) or 0,
        )
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "-a", "my.py:graph", "--serve-check"])
        with pytest.raises(SystemExit):
            main()
        assert called["spec"] == "my.py:graph"

    def test_help_lists_serve_check(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--help"])
        main()
        assert "--serve-check" in capsys.readouterr().out


class TestServeCheckServerSpawn:
    """serve_check() must boot the server so it works in CI/Docker (as root), and
    surface the server's own output when it dies before serving (gh #58)."""

    class _DeadProc:
        """A spawned server that exited immediately, with captured output."""

        returncode = 1

        def __init__(self, output):
            import io

            self.stdout = io.StringIO(output)

        def poll(self):
            return self.returncode  # already exited

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            pass

    def _run_with_fake_server(self, monkeypatch, output):
        captured = {}

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv
            return self._DeadProc(output)

        monkeypatch.setattr("langstage_jupyter.launcher.subprocess.Popen", fake_popen)
        monkeypatch.setattr("langstage_jupyter.launcher.find_available_port", lambda *a, **k: 12321)
        code = serve_check(DEMO_AGENT_SPEC, boot_timeout=1.0)
        return code, captured["argv"]

    def test_spawns_server_with_allow_root(self, monkeypatch, capsys):
        # gh #58: CI/Docker run as root; jupyter_server refuses to boot as root
        # without allow_root, so the spawned server MUST carry it.
        code, argv = self._run_with_fake_server(monkeypatch, "boom\n")
        assert code == 1  # our fake server "died", so the check fails...
        assert any("allow_root=True" in a for a in argv), argv  # ...but with the flag set

    def test_early_exit_surfaces_server_output(self, monkeypatch, capsys):
        # gh #58: the real cause used to be swallowed — the verdict must include the
        # server's own last lines (e.g. the root guard), not just an exit code.
        code, _ = self._run_with_fake_server(
            monkeypatch,
            "some noise\nRunning as root is not recommended. Use --allow-root to bypass.\n",
        )
        out = capsys.readouterr().out
        assert code == 1
        assert "exited before it was ready" in out
        assert "Running as root" in out  # the actual diagnostic is now shown


@pytest.mark.skipif(
    os.environ.get("RUN_SERVE_CHECK_IT") != "1",
    reason="real jupyter-server boot; opt in with RUN_SERVE_CHECK_IT=1 (kept out of the "
    "release matrix to avoid per-cell boot time/flakiness — the served path is proven here)",
)
def test_serve_check_end_to_end_with_demo_agent():
    """The real thing: boot the server extension headlessly and serve one turn."""
    assert serve_check(DEMO_AGENT_SPEC) == 0


class TestConnectionVerdict:
    """_connection_verdict reduces a /api/status probe to (exit_code, message) — the
    verdict logic behind --check-connection, unit-tested without a server (gh #67)."""

    def test_ok_with_version(self):
        code, msg = _connection_verdict("http://localhost:8888", status=200, server_version="2.20.0")
        assert code == 0
        assert msg.startswith("[ ok ]")
        assert "token accepted" in msg
        assert "2.20.0" in msg

    def test_ok_without_version_omits_suffix(self):
        code, msg = _connection_verdict("http://localhost:8888", status=200)
        assert code == 0 and "token accepted" in msg
        assert "Jupyter Server" not in msg  # no dangling empty parens

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_failure_names_the_token_var(self, status):
        # URL right, token wrong — the distinct failure mode this enhancement is about.
        code, msg = _connection_verdict("http://localhost:8888", status=status)
        assert code == 1
        assert str(status) in msg
        assert "LANGSTAGE_JUPYTER_TOKEN" in msg
        assert "IdentityProvider.token" in msg

    def test_unreachable_names_the_url_and_server(self):
        code, msg = _connection_verdict("http://localhost:8888", unreachable=True)
        assert code == 1
        assert "unreachable" in msg
        assert "LANGSTAGE_JUPYTER_SERVER_URL" in msg

    def test_unexpected_status_fails(self):
        code, msg = _connection_verdict("http://localhost:8888", status=500)
        assert code == 1 and "500" in msg

    def test_client_error_is_reported(self):
        code, msg = _connection_verdict("http://localhost:8888", error="boom")
        assert code == 1 and "boom" in msg


class _FakeResp:
    def __init__(self, code, body=b"{}"):
        self._code = code
        self._body = body

    def getcode(self):
        return self._code

    def read(self):
        return self._body


class TestCheckConnection:
    """check_connection() resolves the configured URL+token and probes /api/status,
    naming the distinct reachable/auth failure modes (gh #67)."""

    def _configure(self, monkeypatch, url="http://localhost:8888", token="tok"):
        monkeypatch.setenv("LANGSTAGE_JUPYTER_SERVER_URL", url)
        monkeypatch.setenv("LANGSTAGE_JUPYTER_TOKEN", token)

    def test_token_accepted_returns_zero_with_version(self, monkeypatch, capsys):
        self._configure(monkeypatch)
        import urllib.request

        def fake_urlopen(req, timeout=None):
            if req.full_url.endswith("/api/status"):
                # the token actually reaches an authenticated endpoint
                assert req.get_header("Authorization") == "token tok"
                return _FakeResp(200)
            return _FakeResp(200, b'{"version": "2.20.0"}')  # /api version enrichment

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert check_connection() == 0
        out = capsys.readouterr().out
        assert "[ ok ]" in out and "2.20.0" in out

    def test_wrong_token_returns_403(self, monkeypatch, capsys):
        self._configure(monkeypatch, token="wrong")
        import urllib.error
        import urllib.request

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert check_connection() == 1
        out = capsys.readouterr().out
        assert "403" in out and "LANGSTAGE_JUPYTER_TOKEN" in out

    def test_unreachable_returns_one(self, monkeypatch, capsys):
        self._configure(monkeypatch)
        import urllib.error
        import urllib.request

        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError("Connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert check_connection() == 1
        assert "unreachable" in capsys.readouterr().out

    def test_version_enrichment_failure_never_fails_the_check(self, monkeypatch, capsys):
        # The optional /api version fetch must not turn a token-accepted result into a fail.
        self._configure(monkeypatch)
        import urllib.error
        import urllib.request

        def fake_urlopen(req, timeout=None):
            if req.full_url.endswith("/api/status"):
                return _FakeResp(200)
            raise urllib.error.URLError("no /api")  # version enrichment blows up

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert check_connection() == 0
        out = capsys.readouterr().out
        assert "[ ok ]" in out and "Jupyter Server" not in out

    def test_missing_server_url_fails_fast(self, monkeypatch, capsys):
        # An empty URL can't be checked — name the var rather than throwing.
        monkeypatch.setenv("LANGSTAGE_JUPYTER_SERVER_URL", "")
        monkeypatch.setenv("DEEPAGENT_JUPYTER_SERVER_URL", "")
        # LabConfig has a non-empty default jupyter_server_url, so force the resolved value
        # empty to exercise the guard.
        from langstage_jupyter import config as _config

        class _Cfg:
            jupyter_server_url = ""
            jupyter_token = "tok"

        monkeypatch.setattr(_config.LabConfig, "resolve", classmethod(lambda cls, *a, **k: _Cfg()))
        assert check_connection() == 1
        assert "LANGSTAGE_JUPYTER_SERVER_URL is not set" in capsys.readouterr().out


class TestCheckConnectionRouting:
    """main() routes --check-connection / --check-server to check_connection() (gh #67)."""

    @pytest.mark.parametrize("flag", ["--check-connection", "--check-server"])
    def test_flag_calls_check_connection_and_exits_with_its_code(self, flag, monkeypatch):
        called = {}

        def fake_check(**kw):
            called["hit"] = True
            return 0

        monkeypatch.setattr("langstage_jupyter.launcher.check_connection", fake_check)
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", flag])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        assert called.get("hit") is True  # routed here, not to jupyter lab

    def test_nonzero_code_propagates(self, monkeypatch):
        monkeypatch.setattr("langstage_jupyter.launcher.check_connection", lambda **kw: 1)
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--check-connection"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1

    def test_help_lists_check_connection(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--help"])
        main()
        assert "--check-connection" in capsys.readouterr().out


# ── port auto-detection scans far enough for many concurrent sessions ──


def _fake_socket_with_busy(monkeypatch, busy):
    """Simulate a machine where `busy` ports are already taken."""
    from langstage_jupyter import launcher as _l

    class FakeSock:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def bind(self, addr):
            if addr[1] in busy:
                raise OSError("address in use")

    monkeypatch.setattr(_l.socket, "socket", FakeSock)


def test_find_available_port_scans_past_the_old_ten_port_window(monkeypatch):
    # 11 busy ports: the old max_attempts=10 gave up here, so an 11th concurrent
    # `langstage-jupyter` session failed outright instead of taking the next port.
    _fake_socket_with_busy(monkeypatch, set(range(8888, 8899)))
    assert find_available_port() == 8899


def test_find_available_port_supports_many_concurrent_sessions(monkeypatch):
    # ~100 sessions' worth of headroom by default (8888-8987).
    _fake_socket_with_busy(monkeypatch, set(range(8888, 8987)))
    assert find_available_port() == 8987


def test_port_scan_width_is_configurable(monkeypatch):
    monkeypatch.setenv("LANGSTAGE_JUPYTER_PORT_ATTEMPTS", "3")
    _fake_socket_with_busy(monkeypatch, set(range(8888, 8891)))
    with pytest.raises(RuntimeError) as e:
        find_available_port()
    assert "8888-8890" in str(e.value)  # exactly 3 tried, no off-by-one


def test_exhausted_port_scan_tells_you_what_to_do(monkeypatch):
    monkeypatch.setenv("LANGSTAGE_JUPYTER_PORT_ATTEMPTS", "2")
    _fake_socket_with_busy(monkeypatch, set(range(8888, 8890)))
    with pytest.raises(RuntimeError) as e:
        find_available_port()
    msg = str(e.value)
    assert "--port" in msg and "LANGSTAGE_JUPYTER_PORT_ATTEMPTS" in msg


def test_bad_port_attempts_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("LANGSTAGE_JUPYTER_PORT_ATTEMPTS", "not-a-number")
    _fake_socket_with_busy(monkeypatch, set(range(8888, 8899)))
    assert find_available_port() == 8899  # didn't crash, used the 100-wide default
