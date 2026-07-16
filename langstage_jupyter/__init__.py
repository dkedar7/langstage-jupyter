"""
JupyterLab DeepAgents Extension
"""
try:
    from ._version import __version__
except ImportError:
    # _version.py is generated at build time by hatchling's version hook from
    # package.json (see [tool.hatch.build.hooks.version]) and is not tracked in
    # git. Importing from a source checkout that hasn't been built yet won't have
    # it, so fall back to the installed package metadata, then a dev sentinel —
    # keeping `import langstage_jupyter` working instead of hard-failing.
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            __version__ = version("langstage-jupyter")
        except PackageNotFoundError:
            __version__ = "0.0.0.dev0"
    except ImportError:
        __version__ = "0.0.0.dev0"

from .handlers import setup_handlers


def _jupyter_labextension_paths():
    """Called by JupyterLab to get extension paths."""
    return [{
        "src": "labextension",
        "dest": "langstage-jupyter"
    }]


def _jupyter_server_extension_points():
    """Called by Jupyter Server to get server extension points."""
    return [{
        "module": "langstage_jupyter"
    }]


def _load_jupyter_server_extension(server_app):
    """Called by Jupyter Server to load the extension."""
    setup_handlers(server_app.web_app)
    server_app.log.info("Loaded langstage-jupyter extension")
