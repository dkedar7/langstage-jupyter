"""
Tests for launcher utilities (launcher.py).
"""
import socket
import pytest
from unittest.mock import Mock, patch, MagicMock
from langstage_jupyter.launcher import (
    DEMO_AGENT_SPEC,
    extract_agent_args,
    find_available_port,
    generate_token,
    main,
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

    def _run_main(self, argv, monkeypatch):
        calls = {}

        def fake_run(cmd, env=None):
            calls["cmd"] = cmd
            calls["env_spec"] = (env or {}).get("LANGSTAGE_AGENT_SPEC")

        monkeypatch.setattr("langstage_jupyter.launcher.subprocess.run", fake_run)
        monkeypatch.setattr("sys.argv", ["langstage-jupyter"] + argv)
        monkeypatch.delenv("LANGSTAGE_AGENT_SPEC", raising=False)
        monkeypatch.delenv("DEEPAGENT_AGENT_SPEC", raising=False)
        main()
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

    def test_demo_and_agent_conflict(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--demo", "-a", "x.py:g"])
        with pytest.raises(SystemExit):
            main()


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

            with pytest.raises(RuntimeError, match="Could not find available port"):
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
