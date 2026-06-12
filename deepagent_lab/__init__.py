"""Deprecated alias package: ``deepagent_lab`` is now ``langstage_jupyter``.

Kept for one transition window so existing imports keep working. Import
``langstage_jupyter`` instead. (The Jupyter server extension registers under
``langstage_jupyter`` only — this alias is import-compat, not a second
extension registration.)
"""
import sys as _sys
import warnings as _warnings

import langstage_jupyter as _new
from langstage_jupyter import *  # noqa: F401,F403
from langstage_jupyter import agent_wrapper, config, launcher  # noqa: F401

_warnings.warn(
    "deepagent_lab has been renamed to langstage_jupyter; "
    "this alias package will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

_sys.modules[__name__ + ".agent_wrapper"] = agent_wrapper
_sys.modules[__name__ + ".config"] = config
_sys.modules[__name__ + ".launcher"] = launcher
__version__ = getattr(_new, "__version__", "0")
