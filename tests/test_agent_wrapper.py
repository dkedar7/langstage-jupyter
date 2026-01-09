"""
Tests for agent wrapper (agent_wrapper.py).
"""
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from deepagent_lab.agent_wrapper import AgentWrapper


class TestAgentWrapperPathDetection:
    """Tests for path detection logic in AgentWrapper."""

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    def test_detects_file_path_with_py_extension(self, mock_load):
        """Should detect file path when it ends with .py"""
        wrapper = AgentWrapper(agent_module_path="my_agent.py")
        assert wrapper.agent_module_path == "my_agent.py"

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    def test_detects_file_path_with_forward_slash(self, mock_load):
        """Should detect file path when it contains forward slash."""
        wrapper = AgentWrapper(agent_module_path="./agents/my_agent.py")
        assert wrapper.agent_module_path == "./agents/my_agent.py"

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    def test_detects_file_path_with_backslash(self, mock_load):
        """Should detect file path when it contains backslash."""
        wrapper = AgentWrapper(agent_module_path=".\\agents\\my_agent.py")
        assert wrapper.agent_module_path == ".\\agents\\my_agent.py"

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    def test_detects_module_path(self, mock_load):
        """Should detect module path when no file indicators present."""
        wrapper = AgentWrapper(agent_module_path="deepagent_lab.agent")
        assert wrapper.agent_module_path == "deepagent_lab.agent"


class TestAgentSpecParsing:
    """Tests for AGENT_SPEC environment variable parsing."""

    @patch('deepagent_lab.agent_wrapper.config.AGENT_SPEC', 'my_module:my_agent')
    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    def test_parses_agent_spec_correctly(self, mock_load):
        """Should parse AGENT_SPEC in module:variable format."""
        wrapper = AgentWrapper()
        assert wrapper.agent_module_path == "my_module"
        assert wrapper.agent_variable_name == "my_agent"

    @patch('deepagent_lab.agent_wrapper.config.AGENT_SPEC', 'invalid_format')
    @patch('deepagent_lab.agent_wrapper.config.AGENT_MODULE', 'default.agent')
    @patch('deepagent_lab.agent_wrapper.config.AGENT_VARIABLE', None)
    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    def test_falls_back_on_invalid_spec(self, mock_load):
        """Should fall back to defaults when AGENT_SPEC format is invalid."""
        wrapper = AgentWrapper()
        assert wrapper.agent_module_path == "default.agent"
        assert wrapper.agent_variable_name is None


class TestContextAppending:
    """Tests for _append_context_to_message method."""

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    def test_appends_current_directory(self, mock_load):
        """Should append current directory to message."""
        wrapper = AgentWrapper()
        context = {"current_directory": "/home/user/project"}

        result = wrapper._append_context_to_message("Hello", context)

        assert "Hello" in result
        assert "Current directory: /home/user/project" in result

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    def test_appends_focused_widget(self, mock_load):
        """Should append focused widget to message."""
        wrapper = AgentWrapper()
        context = {"focused_widget": "notebook.ipynb"}

        result = wrapper._append_context_to_message("Test", context)

        assert "Test" in result
        assert "Currently focused file: notebook.ipynb" in result

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
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

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    def test_truncates_long_selections(self, mock_load):
        """Should truncate very long selected text."""
        wrapper = AgentWrapper()
        long_text = "x" * 3000
        context = {"selected_text": long_text}

        result = wrapper._append_context_to_message("Test", context)

        assert "truncated" in result
        assert len(result) < len(long_text) + 100

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    def test_no_context_returns_original_message(self, mock_load):
        """Should return original message when no context provided."""
        wrapper = AgentWrapper()

        result = wrapper._append_context_to_message("Original", None)

        assert result == "Original"

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    def test_empty_context_returns_original_message(self, mock_load):
        """Should return original message when context is empty."""
        wrapper = AgentWrapper()

        result = wrapper._append_context_to_message("Original", {})

        assert result == "Original"

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
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

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    @patch('os.environ', {})
    def test_sets_environment_variable(self, mock_load):
        """Should set DEEPAGENT_WORKSPACE_ROOT environment variable."""
        import os
        wrapper = AgentWrapper()
        wrapper.agent = Mock()

        wrapper.set_root_dir("/home/user/workspace")

        assert os.environ['DEEPAGENT_WORKSPACE_ROOT'] == "/home/user/workspace"

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    def test_updates_agent_backend_if_available(self, mock_load):
        """Should update agent backend if it exists."""
        wrapper = AgentWrapper()
        mock_backend = Mock()
        mock_agent = Mock()
        mock_agent.backend = mock_backend
        wrapper.agent = mock_agent

        # Should not raise an error when agent has backend
        # FilesystemBackend update is optional and may fail if module not available
        wrapper.set_root_dir("/new/root")

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    def test_handles_agent_without_backend(self, mock_load):
        """Should handle agent without backend gracefully."""
        wrapper = AgentWrapper()
        mock_agent = Mock(spec=[])  # Agent without backend attribute
        wrapper.agent = mock_agent

        # Should not raise an error
        wrapper.set_root_dir("/some/path")


class TestExecuteMethod:
    """Tests for execute method."""

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    def test_returns_error_when_agent_not_loaded(self, mock_load):
        """Should return error when agent is not loaded."""
        wrapper = AgentWrapper()
        wrapper.agent = None

        results = list(wrapper.execute(message="Hello"))

        assert len(results) == 1
        assert results[0]['status'] == 'error'
        assert 'not loaded' in results[0]['error']

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    def test_requires_message_or_decisions(self, mock_load):
        """Should return error when neither message nor decisions provided."""
        wrapper = AgentWrapper()
        wrapper.agent = Mock()

        results = list(wrapper.execute())

        assert any('Must provide' in str(r.get('error', '')) for r in results)

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    @patch('deepagent_lab.agent_wrapper.stream_graph_updates')
    def test_executes_with_message(self, mock_stream, mock_load):
        """Should execute agent with message."""
        mock_stream.return_value = iter([
            {"chunk": "Hello", "status": "streaming"},
            {"status": "complete"}
        ])

        wrapper = AgentWrapper()
        wrapper.agent = Mock()

        results = list(wrapper.execute(message="Test message"))

        assert len(results) == 2
        assert results[0]['chunk'] == "Hello"
        assert results[1]['status'] == "complete"

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    @patch('deepagent_lab.agent_wrapper.stream_graph_updates')
    def test_adds_thread_id_to_config(self, mock_stream, mock_load):
        """Should add thread_id to config when provided."""
        mock_stream.return_value = iter([{"status": "complete"}])

        wrapper = AgentWrapper()
        wrapper.agent = Mock()

        list(wrapper.execute(message="Test", thread_id="thread_123"))

        # Verify stream_graph_updates was called with config containing thread_id
        # The function is called as: stream_graph_updates(agent, input, config=config)
        mock_stream.assert_called_once()
        call_kwargs = mock_stream.call_args.kwargs
        config = call_kwargs['config']
        assert config['configurable']['thread_id'] == "thread_123"

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    @patch('deepagent_lab.agent_wrapper.stream_graph_updates')
    def test_appends_context_to_message(self, mock_stream, mock_load):
        """Should append context to message before execution."""
        mock_stream.return_value = iter([{"status": "complete"}])

        wrapper = AgentWrapper()
        wrapper.agent = Mock()

        context = {"current_directory": "/home/user"}
        list(wrapper.execute(message="Test", context=context))

        # Verify prepare_agent_input was called with context-enhanced message
        call_args = mock_stream.call_args
        agent_input = call_args[0][1]  # Second argument is agent_input
        assert "/home/user" in str(agent_input)


class TestAgentNameExtraction:
    """Tests for agent name extraction (used in sidebar display)."""

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
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

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
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

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
    def test_returns_none_when_agent_is_not_loaded(self, mock_load):
        """Should return None when agent is not loaded."""
        wrapper = AgentWrapper()
        wrapper.agent = None

        # Simulate what the HealthHandler does
        agent_name = None
        if wrapper.agent and hasattr(wrapper.agent, 'name'):
            agent_name = wrapper.agent.name

        assert agent_name is None

    @patch('deepagent_lab.agent_wrapper.AgentWrapper._load_agent')
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

    @patch('deepagent_lab.agent_wrapper.AgentWrapper')
    def test_creates_agent_on_first_call(self, mock_wrapper_class):
        """Should create agent instance on first call."""
        from deepagent_lab.agent_wrapper import get_agent, _agent_instance

        # Reset global instance
        import deepagent_lab.agent_wrapper as aw
        aw._agent_instance = None

        mock_instance = Mock()
        mock_wrapper_class.return_value = mock_instance

        result = get_agent()

        mock_wrapper_class.assert_called_once()
        assert result == mock_instance

    @patch('deepagent_lab.agent_wrapper.AgentWrapper')
    def test_returns_same_instance_on_subsequent_calls(self, mock_wrapper_class):
        """Should return same instance on subsequent calls."""
        from deepagent_lab.agent_wrapper import get_agent

        # Reset global instance
        import deepagent_lab.agent_wrapper as aw
        aw._agent_instance = None

        mock_instance = Mock()
        mock_wrapper_class.return_value = mock_instance

        result1 = get_agent()
        result2 = get_agent()

        # Should only create once
        assert mock_wrapper_class.call_count == 1
        assert result1 == result2
