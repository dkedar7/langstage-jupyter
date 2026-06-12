"""The deepagent_lab → langstage_jupyter rename ships a deprecated alias package."""

import sys

import pytest


def test_legacy_import_works_and_warns():
    for name in list(sys.modules):
        if name == "deepagent_lab" or name.startswith("deepagent_lab."):
            sys.modules.pop(name)
    with pytest.warns(DeprecationWarning, match="langstage_jupyter"):
        import deepagent_lab  # noqa: F401


def test_legacy_submodules_alias_the_new_ones():
    import deepagent_lab.config as old_config
    import langstage_jupyter.config as new_config

    assert old_config is new_config

    import deepagent_lab.launcher as old_launcher
    import langstage_jupyter.launcher as new_launcher

    assert old_launcher is new_launcher
