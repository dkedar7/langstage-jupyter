"""
Tests for LangGraph utilities (langgraph_utils.py).
"""
import pytest
from unittest.mock import Mock
from deepagent_lab.langgraph_utils import (
    parse_interrupt_value,
    serialize_action_request,
    serialize_review_config,
    process_interrupt,
    extract_todos_from_content,
    extract_reflection_from_content,
    serialize_tool_calls,
    clean_content_from_tool_dicts,
    process_message_content,
    process_tool_message,
    prepare_agent_input,
)


class TestParseInterruptValue:
    """Tests for parse_interrupt_value function."""

    def test_single_element_tuple_with_dict_value(self):
        """Should parse single-element tuple with Interrupt object containing dict."""
        interrupt_obj = Mock()
        interrupt_obj.value = {
            'action_requests': [{'tool': 'test_tool'}],
            'review_configs': [{'allowed_decisions': ['approve', 'reject']}]
        }
        interrupt_value = (interrupt_obj,)

        action_requests, review_configs = parse_interrupt_value(interrupt_value)

        assert action_requests == [{'tool': 'test_tool'}]
        assert review_configs == [{'allowed_decisions': ['approve', 'reject']}]

    def test_single_element_tuple_with_attributes(self):
        """Should parse single-element tuple with attributes."""
        interrupt_obj = Mock()
        interrupt_obj.action_requests = [{'name': 'action1'}]
        interrupt_obj.review_configs = [{'config': 'test'}]
        interrupt_value = (interrupt_obj,)

        action_requests, review_configs = parse_interrupt_value(interrupt_value)

        assert action_requests == [{'name': 'action1'}]
        assert review_configs == [{'config': 'test'}]

    def test_two_element_tuple(self):
        """Should parse two-element tuple directly."""
        interrupt_value = (
            [{'tool': 'tool1'}, {'tool': 'tool2'}],
            [{'config': 'cfg1'}]
        )

        action_requests, review_configs = parse_interrupt_value(interrupt_value)

        assert action_requests == [{'tool': 'tool1'}, {'tool': 'tool2'}]
        assert review_configs == [{'config': 'cfg1'}]

    def test_object_with_attributes(self):
        """Should parse object with attributes directly."""
        interrupt_obj = Mock()
        interrupt_obj.action_requests = [{'action': 'test'}]
        interrupt_obj.review_configs = []

        action_requests, review_configs = parse_interrupt_value(interrupt_obj)

        assert action_requests == [{'action': 'test'}]
        assert review_configs == []


class TestSerializeActionRequest:
    """Tests for serialize_action_request function."""

    def test_dict_format_with_tool_field(self):
        """Should serialize dict with 'tool' field."""
        action = {
            'tool': 'get_notebook',
            'tool_call_id': 'call_123',
            'args': {'path': 'test.ipynb'},
            'description': 'Get notebook state'
        }

        result = serialize_action_request(action, 0)

        assert result['tool'] == 'get_notebook'
        assert result['tool_call_id'] == 'call_123'
        assert result['args'] == {'path': 'test.ipynb'}
        assert result['description'] == 'Get notebook state'

    def test_dict_format_with_name_field(self):
        """Should serialize dict with 'name' field as fallback."""
        action = {
            'name': 'execute_cell',
            'args': {'cell_id': 5}
        }

        result = serialize_action_request(action, 0)

        assert result['tool'] == 'execute_cell'
        assert result['tool_call_id'] == 'call_0'
        assert result['args'] == {'cell_id': 5}

    def test_object_format(self):
        """Should serialize object with attributes."""
        action = Mock()
        action.tool = 'modify_cell'
        action.tool_call_id = 'call_abc'
        action.args = {'cell_id': 3, 'content': 'new code'}
        action.description = 'Modify cell content'

        result = serialize_action_request(action, 1)

        assert result['tool'] == 'modify_cell'
        assert result['tool_call_id'] == 'call_abc'
        assert result['args'] == {'cell_id': 3, 'content': 'new code'}
        assert result['description'] == 'Modify cell content'


class TestSerializeReviewConfig:
    """Tests for serialize_review_config function."""

    def test_object_format(self):
        """Should serialize review config object."""
        config = Mock()
        config.allowed_decisions = ['approve', 'reject', 'modify']

        result = serialize_review_config(config)

        assert result['allowed_decisions'] == ['approve', 'reject', 'modify']

    def test_dict_format(self):
        """Should serialize review config dict."""
        config = {'allowed_decisions': ['approve', 'reject']}

        result = serialize_review_config(config)

        assert result['allowed_decisions'] == ['approve', 'reject']


class TestExtractTodosFromContent:
    """Tests for extract_todos_from_content function."""

    def test_string_with_array(self):
        """Should extract todos from string containing array."""
        content = "Updated todo list to [{'task': 'Test feature', 'status': 'pending'}]"

        result = extract_todos_from_content(content)

        assert result == [{'task': 'Test feature', 'status': 'pending'}]

    def test_json_string(self):
        """Should parse JSON string."""
        content = '[{"task": "Write tests", "status": "in_progress"}]'

        result = extract_todos_from_content(content)

        assert result == [{'task': 'Write tests', 'status': 'in_progress'}]

    def test_dict_with_todos_key(self):
        """Should extract todos from dict."""
        content = {'todos': [{'task': 'Deploy', 'status': 'pending'}]}

        result = extract_todos_from_content(content)

        assert result == [{'task': 'Deploy', 'status': 'pending'}]

    def test_direct_list(self):
        """Should return list directly."""
        content = [{'task': 'Review code', 'status': 'completed'}]

        result = extract_todos_from_content(content)

        assert result == [{'task': 'Review code', 'status': 'completed'}]

    def test_invalid_content_returns_none(self):
        """Should return None for unparseable content."""
        content = "No todos here"

        result = extract_todos_from_content(content)

        assert result is None


class TestExtractReflectionFromContent:
    """Tests for extract_reflection_from_content function."""

    def test_json_string_with_reflection(self):
        """Should extract reflection from JSON string."""
        content = '{"reflection": "I need to analyze the data more carefully"}'

        result = extract_reflection_from_content(content)

        assert result == "I need to analyze the data more carefully"

    def test_dict_with_reflection(self):
        """Should extract reflection from dict."""
        content = {'reflection': 'The approach seems correct'}

        result = extract_reflection_from_content(content)

        assert result == 'The approach seems correct'

    def test_plain_string(self):
        """Should return plain string as reflection."""
        content = "This is a direct reflection"

        result = extract_reflection_from_content(content)

        assert result == "This is a direct reflection"

    def test_invalid_json_string(self):
        """Should return string as-is if JSON parsing fails."""
        content = "Not valid {json"

        result = extract_reflection_from_content(content)

        assert result == "Not valid {json"


class TestSerializeToolCalls:
    """Tests for serialize_tool_calls function."""

    def test_serialize_dict_tool_calls(self):
        """Should serialize tool calls from dicts."""
        tool_calls = [
            {'id': 'call_1', 'name': 'get_state', 'args': {'path': 'test.ipynb'}},
            {'id': 'call_2', 'name': 'execute', 'args': {'cell': 0}}
        ]

        result = serialize_tool_calls(tool_calls)

        assert len(result) == 2
        assert result[0]['id'] == 'call_1'
        assert result[0]['name'] == 'get_state'
        assert result[1]['id'] == 'call_2'
        assert result[1]['name'] == 'execute'

    def test_serialize_object_tool_calls(self):
        """Should serialize tool calls from objects."""
        tc1 = Mock()
        tc1.id = 'call_obj_1'
        tc1.name = 'modify_cell'
        tc1.args = {'cell_id': 5}

        result = serialize_tool_calls([tc1])

        assert len(result) == 1
        assert result[0]['id'] == 'call_obj_1'
        assert result[0]['name'] == 'modify_cell'
        assert result[0]['args'] == {'cell_id': 5}

    def test_skip_tools(self):
        """Should skip specified tool names."""
        tool_calls = [
            {'id': 'call_1', 'name': 'think_tool', 'args': {}},
            {'id': 'call_2', 'name': 'write_todos', 'args': {}},
            {'id': 'call_3', 'name': 'execute', 'args': {}}
        ]

        result = serialize_tool_calls(tool_calls, skip_tools=['think_tool', 'write_todos'])

        assert len(result) == 1
        assert result[0]['name'] == 'execute'


class TestCleanContentFromToolDicts:
    """Tests for clean_content_from_tool_dicts function."""

    def test_removes_tool_dict_representations(self):
        """Should remove tool call dictionary representations from content."""
        content = "Here is some text {'id': 'call_123', 'input': {'arg': 'value'}, 'name': 'test_tool', 'type': 'tool_use'} more text"

        result = clean_content_from_tool_dicts(content)

        assert result == "Here is some text  more text"

    def test_leaves_regular_content_unchanged(self):
        """Should not modify content without tool dicts."""
        content = "This is regular text without tool calls"

        result = clean_content_from_tool_dicts(content)

        assert result == content


class TestProcessMessageContent:
    """Tests for process_message_content function."""

    def test_string_content(self):
        """Should return string content directly."""
        msg = Mock()
        msg.content = "Hello, world!"

        result = process_message_content(msg)

        assert result == "Hello, world!"

    def test_list_content_with_text_blocks(self):
        """Should join text blocks from list content."""
        msg = Mock()
        msg.content = [
            {"text": "First block", "type": "text"},
            {"text": "Second block", "type": "text"}
        ]

        result = process_message_content(msg)

        assert result == "First block Second block"

    def test_message_without_content(self):
        """Should return empty string for message without content."""
        msg = Mock(spec=[])

        result = process_message_content(msg)

        assert result == ""

    def test_other_content_types(self):
        """Should convert other content types to string."""
        msg = Mock()
        msg.content = 12345

        result = process_message_content(msg)

        assert result == "12345"


class TestProcessToolMessage:
    """Tests for process_tool_message function."""

    def test_think_tool_message(self):
        """Should extract reflection from think_tool."""
        msg = Mock()
        msg.name = 'think_tool'
        msg.content = '{"reflection": "I should consider edge cases"}'

        result = process_tool_message(msg)

        assert result is not None
        assert result['chunk'] == "I should consider edge cases"
        assert result['status'] == "streaming"

    def test_write_todos_message(self):
        """Should extract todos from write_todos."""
        msg = Mock()
        msg.name = 'write_todos'
        msg.content = '[{"task": "Test feature", "status": "pending"}]'

        result = process_tool_message(msg)

        assert result is not None
        assert result['todo_list'] == [{"task": "Test feature", "status": "pending"}]
        assert result['status'] == "streaming"

    def test_other_tool_message(self):
        """Should return None for other tools."""
        msg = Mock()
        msg.name = 'execute_cell'
        msg.content = 'Cell executed successfully'

        result = process_tool_message(msg)

        assert result is None

    def test_message_without_name(self):
        """Should return None for message without name attribute."""
        msg = Mock(spec=['content'])
        msg.content = 'Some content'

        result = process_tool_message(msg)

        assert result is None


class TestPrepareAgentInput:
    """Tests for prepare_agent_input function."""

    def test_message_input(self):
        """Should prepare message input correctly."""
        result = prepare_agent_input(message="Hello, agent!")

        assert result == {"messages": [{"role": "user", "content": "Hello, agent!"}]}

    def test_raw_input(self):
        """Should pass raw input through unchanged."""
        raw = {"custom": "format", "data": [1, 2, 3]}

        result = prepare_agent_input(raw_input=raw)

        assert result == raw

    def test_decisions_input(self):
        """Should prepare decisions input with Command."""
        from langgraph.types import Command

        decisions = [{"type": "approve", "tool_call_id": "call_123"}]

        result = prepare_agent_input(decisions=decisions)

        assert isinstance(result, Command)
        assert result.resume == {"decisions": decisions}

    def test_no_input_raises_error(self):
        """Should raise ValueError when no input provided."""
        with pytest.raises(ValueError, match="Must provide one of"):
            prepare_agent_input()

    def test_multiple_inputs_raises_error(self):
        """Should raise ValueError when multiple inputs provided."""
        with pytest.raises(ValueError, match="Can only provide one of"):
            prepare_agent_input(message="Hello", decisions=[])
