"""
JupyterLab DeepAgents Extension
"""
from ._version import __version__
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
