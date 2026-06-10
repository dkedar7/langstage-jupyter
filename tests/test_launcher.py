"""
Tests for launcher utilities (launcher.py).
"""
import socket
import pytest
from unittest.mock import Mock, patch, MagicMock
from deepagent_lab.launcher import (
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
            calls["env_spec"] = (env or {}).get("DEEPAGENT_AGENT_SPEC")

        monkeypatch.setattr("deepagent_lab.launcher.subprocess.run", fake_run)
        monkeypatch.setattr("sys.argv", ["deepagent-lab"] + argv)
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
        monkeypatch.setattr("sys.argv", ["deepagent-lab", "--demo", "-a", "x.py:g"])
        with pytest.raises(SystemExit):
            main()


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
