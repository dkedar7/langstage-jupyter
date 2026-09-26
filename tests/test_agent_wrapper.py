"""
Tests for agent wrapper (agent_wrapper.py).
"""
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from langstage_jupyter.agent_wrapper import AgentWrapper


class TestAgentWrapperPathDetection:
    """Tests for path detection logic in AgentWrapper."""

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_detects_file_path_with_py_extension(self, mock_load):
        """Should detect file path when it ends with .py"""
        wrapper = AgentWrapper(agent_module_path="my_agent.py")
        assert wrapper.agent_module_path == "my_agent.py"

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_detects_file_path_with_forward_slash(self, mock_load):
        """Should detect file path when it contains forward slash."""
        wrapper = AgentWrapper(agent_module_path="./agents/my_agent.py")
        assert wrapper.agent_module_path == "./agents/my_agent.py"

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_detects_file_path_with_backslash(self, mock_load):
        """Should detect file path when it contains backslash."""
        wrapper = AgentWrapper(agent_module_path=".\\agents\\my_agent.py")
        assert wrapper.agent_module_path == ".\\agents\\my_agent.py"

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_detects_module_path(self, mock_load):
        """Should detect module path when no file indicators present."""
        wrapper = AgentWrapper(agent_module_path="langstage_jupyter.agent")
        assert wrapper.agent_module_path == "langstage_jupyter.agent"


class TestAgentSpecParsing:
    """Tests for AGENT_SPEC environment variable parsing."""

    @patch('langstage_jupyter.agent_wrapper.config.AGENT_SPEC', 'my_module:my_agent')
    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_parses_agent_spec_correctly(self, mock_load):
        """Should parse AGENT_SPEC in module:variable format."""
        wrapper = AgentWrapper()
        assert wrapper.agent_module_path == "my_module"
        assert wrapper.agent_variable_name == "my_agent"

    @patch('langstage_jupyter.agent_wrapper.config.AGENT_SPEC', 'my_agent.py')
    @patch('langstage_jupyter.agent_wrapper.config.AGENT_MODULE', 'default.agent')
    @patch('langstage_jupyter.agent_wrapper.config.AGENT_VARIABLE', None)
    def test_colon_less_spec_is_an_error_not_a_default_fallback(self):
        """gh #151: a colon-less spec must not silently load the default agent.

        The old local split(':') printed a warning and fell back to AGENT_MODULE (the
        bundled default), so a typo ran a different agent than asked. Now core's
        parse_agent_spec rejects it: nothing loads, and the error is kept for /health.
        """
        with patch('langstage_jupyter.agent_wrapper.load_agent_spec') as load:
            wrapper = AgentWrapper()
        load.assert_not_called()  # in particular, never 'default.agent:...'
        assert wrapper.agent is None
        assert wrapper.agent_module_path is None
        assert "my_agent.py:graph" in wrapper.load_error  # core's hint
        # A reload keeps refusing rather than falling back.
        with patch('langstage_jupyter.agent_wrapper.load_agent_spec') as load:
            wrapper.reload_agent()
        load.assert_not_called()
        assert wrapper.agent is None

    @pytest.mark.parametrize("spec", ["my_agent.py", "pkg.mod", "agent.py:", ":graph"])
    def test_resolve_agent_target_raises_for_a_malformed_spec(self, spec):
        """gh #151: the shared resolver (--verify / --ask / --serve-check) raises."""
        with pytest.raises(ValueError, match="Invalid agent spec"):
            AgentWrapper.resolve_agent_target(spec, "default.agent", None)

    def test_resolve_agent_target_keeps_a_windows_drive_path(self):
        """Core splits on the LAST ':' and strips, so a drive-letter path survives."""
        assert AgentWrapper.resolve_agent_target(
            "  C:\\x\\agent.py:graph ", "default.agent", None
        ) == ("C:\\x\\agent.py", "graph")

    def test_resolve_agent_target_does_not_claim_the_environment(self, capsys):
        """gh #119: the resolver can't know where the spec came from (toml, -a, --demo
        or env), so its status line must not assert "from environment"."""
        AgentWrapper.resolve_agent_target("./a.py:graph", "default.agent", None)
        out = capsys.readouterr().out
        assert "from environment" not in out
        assert "Using agent: ./a.py:graph" in out


class TestContextAppending:
    """Tests for _append_context_to_message method."""

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_appends_current_directory(self, mock_load):
        """Should append current directory to message."""
        wrapper = AgentWrapper()
        context = {"current_directory": "/home/user/project"}

        result = wrapper._append_context_to_message("Hello", context)

        assert "Hello" in result
        assert "Current directory: /home/user/project" in result

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_appends_focused_widget(self, mock_load):
        """Should append focused widget to message."""
        wrapper = AgentWrapper()
        context = {"focused_widget": "notebook.ipynb"}

        result = wrapper._append_context_to_message("Test", context)

        assert "Test" in result
        assert "Currently focused file: notebook.ipynb" in result

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_appends_selected_text(self, mock_load):
        """Should append selected text to message."""
        wrapper = AgentWrapper()
        context = {
            "selected_text": "def hello():\n    print('world')",
            "selection_metadata": "cell_0"
        }

        result = wrapper._append_context_to_message("Explain this", context)

        assert "Explain this" in result
        assert "User has selected the following text" in result
        assert "from cell index 0" in result
        assert "def hello():" in result

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_truncates_long_selections(self, mock_load):
        """Should truncate very long selected text."""
        wrapper = AgentWrapper()
        long_text = "x" * 3000
        context = {"selected_text": long_text}

        result = wrapper._append_context_to_message("Test", context)

        assert "truncated" in result
        assert len(result) < len(long_text) + 100

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_no_context_returns_original_message(self, mock_load):
        """Should return original message when no context provided."""
        wrapper = AgentWrapper()

        result = wrapper._append_context_to_message("Original", None)

        assert result == "Original"

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_empty_context_returns_original_message(self, mock_load):
        """Should return original message when context is empty."""
        wrapper = AgentWrapper()

        result = wrapper._append_context_to_message("Original", {})

        assert result == "Original"

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_combines_multiple_context_parts(self, mock_load):
        """Should combine all context parts when multiple are provided."""
        wrapper = AgentWrapper()
        context = {
            "current_directory": "/home/user",
            "focused_widget": "test.py",
            "selected_text": "code"
        }

        result = wrapper._append_context_to_message("Message", context)

        assert "Message" in result
        assert "Current directory: /home/user" in result
        assert "Currently focused file: test.py" in result
        assert "User has selected the following text" in result


class TestSetRootDir:
    """Tests for set_root_dir method."""

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    @patch('os.environ', {})
    def test_sets_environment_variable(self, mock_load, tmp_path):
        """Should publish BOTH the canonical and legacy workspace-root env vars.

        Since ADR 0005 the published value is the *resolved* absolute root (what
        apply_workspace records as the source of truth), not the raw string. Uses a
        real dir — apply_workspace ensures the root exists (in a real session it's
        JupyterLab's live launch dir, which always exists).
        """
        import os

        wrapper = AgentWrapper()
        wrapper.agent = Mock()

        wrapper.set_root_dir(str(tmp_path))
        expected = str(tmp_path.resolve())

        # Canonical name is what the README's custom-agent example reads.
        assert os.environ['LANGSTAGE_WORKSPACE_ROOT'] == expected
        # Legacy name stays set for back-compat with anything still reading it.
        assert os.environ['DEEPAGENT_WORKSPACE_ROOT'] == expected

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_updates_agent_backend_if_available(self, mock_load, tmp_path):
        """Should update agent backend if it exists."""
        wrapper = AgentWrapper()
        mock_backend = Mock()
        mock_agent = Mock()
        mock_agent.backend = mock_backend
        wrapper.agent = mock_agent

        # Should not raise an error when agent has backend
        # FilesystemBackend update is optional and may fail if module not available
        wrapper.set_root_dir(str(tmp_path / "new"))

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_handles_agent_without_backend(self, mock_load, tmp_path):
        """Should handle agent without backend gracefully."""
        wrapper = AgentWrapper()
        mock_agent = Mock(spec=[])  # Agent without backend attribute
        wrapper.agent = mock_agent

        # Should not raise an error
        wrapper.set_root_dir(str(tmp_path / "some"))


class TestExecuteMethod:
    """Tests for execute method."""

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_returns_error_when_agent_not_loaded(self, mock_load):
        """Should return error when agent is not loaded."""
        wrapper = AgentWrapper()
        wrapper.agent = None

        results = list(wrapper.execute(message="Hello"))

        assert len(results) == 1
        assert results[0]['status'] == 'error'
        assert 'not loaded' in results[0]['error']

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_requires_message_or_decisions(self, mock_load):
        """Should return error when neither message nor decisions provided."""
        wrapper = AgentWrapper()
        wrapper.agent = Mock()

        results = list(wrapper.execute())

        assert any('Must provide' in str(r.get('error', '')) for r in results)

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    @patch('langstage_jupyter.agui_stream.stream_updates_sync')
    def test_executes_with_message(self, mock_stream, mock_load):
        """Should execute agent with message through the AG-UI adapter."""
        mock_stream.return_value = iter([
            {"chunk": "Hello", "status": "streaming"},
            {"status": "complete"}
        ])

        wrapper = AgentWrapper()
        wrapper.agent = Mock()
        wrapper._agui_agent = Mock()  # pre-built, so execute() skips build_session_agent

        results = list(wrapper.execute(message="Test message"))

        assert len(results) == 2
        assert results[0]['chunk'] == "Hello"
        assert results[1]['status'] == "complete"

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    @patch('langstage_jupyter.agui_stream.stream_updates_sync')
    def test_adds_thread_id_to_config(self, mock_stream, mock_load):
        """Should pass thread_id through as the AG-UI turn's thread id."""
        mock_stream.return_value = iter([{"status": "complete"}])

        wrapper = AgentWrapper()
        wrapper.agent = Mock()
        wrapper._agui_agent = Mock()

        list(wrapper.execute(message="Test", thread_id="thread_123"))

        # stream_updates_sync(agent, message, thread_id, resume=...)
        mock_stream.assert_called_once()
        assert mock_stream.call_args.args[2] == "thread_123"

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    @patch('langstage_jupyter.agui_stream.stream_updates_sync')
    def test_appends_context_to_message(self, mock_stream, mock_load):
        """Should append context to the message before streaming."""
        mock_stream.return_value = iter([{"status": "complete"}])

        wrapper = AgentWrapper()
        wrapper.agent = Mock()
        wrapper._agui_agent = Mock()

        context = {"current_directory": "/home/user"}
        list(wrapper.execute(message="Test", context=context))

        # stream_updates_sync(agent, message, thread_id, ...) — message is 2nd positional
        assert "/home/user" in str(mock_stream.call_args.args[1])


class TestAgentNameExtraction:
    """Tests for agent name extraction (used in sidebar display)."""

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_extracts_name_when_agent_has_name_attribute(self, mock_load):
        """Should extract name from agent when name attribute exists."""
        wrapper = AgentWrapper()
        mock_agent = Mock()
        mock_agent.name = "MyCustomAgent"
        wrapper.agent = mock_agent

        # Simulate what the HealthHandler does
        agent_name = None
        if wrapper.agent and hasattr(wrapper.agent, 'name'):
            agent_name = wrapper.agent.name

        assert agent_name == "MyCustomAgent"

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_returns_none_when_agent_has_no_name_attribute(self, mock_load):
        """Should return None when agent doesn't have name attribute."""
        wrapper = AgentWrapper()
        mock_agent = Mock(spec=[])  # Agent without name attribute
        wrapper.agent = mock_agent

        # Simulate what the HealthHandler does
        agent_name = None
        if wrapper.agent and hasattr(wrapper.agent, 'name'):
            agent_name = wrapper.agent.name

        assert agent_name is None

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_returns_none_when_agent_is_not_loaded(self, mock_load):
        """Should return None when agent is not loaded."""
        wrapper = AgentWrapper()
        wrapper.agent = None

        # Simulate what the HealthHandler does
        agent_name = None
        if wrapper.agent and hasattr(wrapper.agent, 'name'):
            agent_name = wrapper.agent.name

        assert agent_name is None

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper._load_agent')
    def test_handles_various_name_types(self, mock_load):
        """Should handle different types of name values."""
        wrapper = AgentWrapper()

        # Test with string name
        mock_agent = Mock()
        mock_agent.name = "Agent v1.0"
        wrapper.agent = mock_agent
        assert hasattr(wrapper.agent, 'name')
        assert wrapper.agent.name == "Agent v1.0"

        # Test with empty string name
        mock_agent.name = ""
        wrapper.agent = mock_agent
        assert wrapper.agent.name == ""


class TestGetAgent:
    """Tests for get_agent singleton function."""

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper')
    def test_creates_agent_on_first_call(self, mock_wrapper_class):
        """Should create agent instance on first call."""
        from langstage_jupyter.agent_wrapper import get_agent, _agent_instance

        # Reset global instance
        import langstage_jupyter.agent_wrapper as aw
        aw._agent_instance = None

        mock_instance = Mock()
        mock_wrapper_class.return_value = mock_instance

        result = get_agent()

        mock_wrapper_class.assert_called_once()
        assert result == mock_instance

    @patch('langstage_jupyter.agent_wrapper.AgentWrapper')
    def test_returns_same_instance_on_subsequent_calls(self, mock_wrapper_class):
        """Should return same instance on subsequent calls."""
        from langstage_jupyter.agent_wrapper import get_agent

        # Reset global instance
        import langstage_jupyter.agent_wrapper as aw
        aw._agent_instance = None

        mock_instance = Mock()
        mock_wrapper_class.return_value = mock_instance

        result1 = get_agent()
        result2 = get_agent()

        # Should only create once
        assert mock_wrapper_class.call_count == 1
        assert result1 == result2
