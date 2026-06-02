"""
Configuration management for deepagent-lab.

This module provides a centralized configuration system with environment variable
support. All environment variables use the DEEPAGENT_ prefix for consistency
with deepagent-dash, allowing agents to be shared between the two libraries.

Usage:
    from deepagent_lab.config import get_config, WORKSPACE_ROOT, JUPYTER_TOKEN

    # Use predefined constants
    workspace = WORKSPACE_ROOT
    token = JUPYTER_TOKEN

    # Or get custom configuration
    custom_value = get_config("custom_key", default="default_value")
"""
import os
from pathlib import Path
from typing import Optional, Callable, Any


def get_config(key: str, default: Any = None, type_cast: Optional[Callable] = None) -> Any:
    """
    Get configuration value with environment variable override.

    Priority:
    1. Environment variable DEEPAGENT_{KEY}
    2. Default value

    Args:
        key: Configuration key (will be uppercased for env var)
        default: Default value if env var not set
        type_cast: Optional function to cast env var value

    Returns:
        Configuration value with appropriate type

    Examples:
        >>> # String configuration
        >>> get_config("agent_module", default="deepagent_lab.agent")
        'deepagent_lab.agent'

        >>> # Integer configuration
        >>> get_config("port", default=8888, type_cast=int)
        8888

        >>> # Boolean configuration
        >>> get_config("debug", default=False,
        ...            type_cast=lambda x: str(x).lower() in ("true", "1", "yes"))
        False

        >>> # With environment variable set
        >>> os.environ['DEEPAGENT_PORT'] = '9999'
        >>> get_config("port", default=8888, type_cast=int)
        9999
    """
    env_key = f"DEEPAGENT_{key.upper()}"
    env_value = os.getenv(env_key)

    if env_value is not None:
        return type_cast(env_value) if type_cast else env_value
    return default


# === Workspace Configuration ===

_workspace_path = get_config("workspace_root", default=None)
WORKSPACE_ROOT: Optional[Path] = Path(_workspace_path).resolve() if _workspace_path else None

# === Agent Configuration ===

# Combined agent spec in format "module_or_file:variable"
# This takes precedence over separate AGENT_MODULE and AGENT_VARIABLE
# Compatible with deepagent-dash
AGENT_SPEC = get_config("agent_spec", default=None)

# Separate module and variable configuration (used if AGENT_SPEC not set)
AGENT_MODULE = get_config("agent_module", default="deepagent_lab.agent")
AGENT_VARIABLE = get_config("agent_variable", default=None)

# === Jupyter Server Configuration ===

JUPYTER_TOKEN = get_config("jupyter_token", default="12345")
JUPYTER_SERVER_URL = get_config("jupyter_server_url", default="http://localhost:8889")

# === Model Configuration ===

MODEL_NAME = get_config("model_name", default="anthropic:claude-sonnet-4-6")
MODEL_TEMPERATURE = get_config("model_temperature", default=0.0, type_cast=float)

# === Debug Configuration ===

DEBUG = get_config(
    "debug",
    default=False,
    type_cast=lambda x: str(x).lower() in ("true", "1", "yes")
)

# === Backend Configuration ===

VIRTUAL_MODE = get_config(
    "virtual_mode",
    default=True,
    type_cast=lambda x: str(x).lower() in ("true", "1", "yes")
)
