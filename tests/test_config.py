"""
Tests for configuration management (config.py).
"""
import os
import pytest
from pathlib import Path
from deepagent_lab.config import get_config


class TestGetConfig:
    """Tests for the get_config function."""

    def test_get_config_returns_default_when_no_env(self, clean_env):
        """Should return default value when environment variable is not set."""
        result = get_config("test_key", default="default_value")
        assert result == "default_value"

    def test_get_config_returns_env_value(self, clean_env, mock_env):
        """Should return environment variable value when set."""
        mock_env("DEEPAGENT_TEST_KEY", "env_value")
        result = get_config("test_key", default="default_value")
        assert result == "env_value"

    def test_get_config_with_type_cast_int(self, clean_env, mock_env):
        """Should cast environment variable to integer."""
        mock_env("DEEPAGENT_PORT", "9999")
        result = get_config("port", default=8888, type_cast=int)
        assert result == 9999
        assert isinstance(result, int)

    def test_get_config_with_type_cast_float(self, clean_env, mock_env):
        """Should cast environment variable to float."""
        mock_env("DEEPAGENT_TEMPERATURE", "0.5")
        result = get_config("temperature", default=0.0, type_cast=float)
        assert result == 0.5
        assert isinstance(result, float)

    def test_get_config_with_type_cast_bool(self, clean_env, mock_env):
        """Should cast environment variable to boolean."""
        bool_cast = lambda x: str(x).lower() in ("true", "1", "yes")

        mock_env("DEEPAGENT_DEBUG", "true")
        assert get_config("debug", default=False, type_cast=bool_cast) is True

        mock_env("DEEPAGENT_DEBUG", "1")
        assert get_config("debug", default=False, type_cast=bool_cast) is True

        mock_env("DEEPAGENT_DEBUG", "yes")
        assert get_config("debug", default=False, type_cast=bool_cast) is True

        mock_env("DEEPAGENT_DEBUG", "false")
        assert get_config("debug", default=True, type_cast=bool_cast) is False

    def test_get_config_uppercases_key(self, clean_env, mock_env):
        """Should uppercase the key when looking up environment variable."""
        mock_env("DEEPAGENT_LOWERCASE_KEY", "value")
        result = get_config("lowercase_key", default="default")
        assert result == "value"

    def test_get_config_returns_default_when_no_type_cast(self, clean_env):
        """Should return default without type casting when env var not set."""
        result = get_config("missing", default=42, type_cast=int)
        assert result == 42

    def test_get_config_with_none_default(self, clean_env):
        """Should handle None as default value."""
        result = get_config("missing_key", default=None)
        assert result is None

    def test_get_config_empty_string_env_value(self, clean_env, mock_env):
        """Should return empty string when env var is set to empty string."""
        mock_env("DEEPAGENT_EMPTY", "")
        result = get_config("empty", default="default")
        assert result == ""


class TestConfigConstants:
    """Tests for exported configuration constants."""

    def test_workspace_root_is_none_by_default(self, clean_env):
        """Should be None when DEEPAGENT_WORKSPACE_ROOT is not set."""
        # Re-import after cleaning env to get fresh values
        import importlib
        from deepagent_lab import config
        importlib.reload(config)
        assert config.WORKSPACE_ROOT is None

    def test_workspace_root_resolves_path(self, clean_env, mock_env, tmp_path):
        """Should resolve workspace root path when set."""
        mock_env("DEEPAGENT_WORKSPACE_ROOT", str(tmp_path))
        import importlib
        from deepagent_lab import config
        importlib.reload(config)
        assert config.WORKSPACE_ROOT == tmp_path.resolve()
        assert isinstance(config.WORKSPACE_ROOT, Path)

    def test_default_values(self, clean_env):
        """Should have expected default values."""
        import importlib
        from deepagent_lab import config
        importlib.reload(config)

        assert config.AGENT_MODULE == "deepagent_lab.agent"
        assert config.AGENT_VARIABLE is None
        assert config.AGENT_SPEC is None
        assert config.JUPYTER_TOKEN == "12345"
        assert config.JUPYTER_SERVER_URL == "http://localhost:8889"
        assert config.MODEL_NAME == "anthropic:claude-sonnet-4-6"
        assert config.MODEL_TEMPERATURE == 0.0
        assert config.DEBUG is False
        assert config.VIRTUAL_MODE is True

    def test_environment_overrides(self, clean_env, mock_env):
        """Should override defaults with environment variables."""
        mock_env("DEEPAGENT_AGENT_MODULE", "custom.agent")
        mock_env("DEEPAGENT_JUPYTER_TOKEN", "custom_token")
        mock_env("DEEPAGENT_JUPYTER_SERVER_URL", "http://localhost:9999")
        mock_env("DEEPAGENT_MODEL_NAME", "openai:gpt-4")
        mock_env("DEEPAGENT_MODEL_TEMPERATURE", "0.7")
        mock_env("DEEPAGENT_DEBUG", "true")
        mock_env("DEEPAGENT_VIRTUAL_MODE", "false")

        import importlib
        from deepagent_lab import config
        importlib.reload(config)

        assert config.AGENT_MODULE == "custom.agent"
        assert config.JUPYTER_TOKEN == "custom_token"
        assert config.JUPYTER_SERVER_URL == "http://localhost:9999"
        assert config.MODEL_NAME == "openai:gpt-4"
        assert config.MODEL_TEMPERATURE == 0.7
        assert config.DEBUG is True
        assert config.VIRTUAL_MODE is False
