"""Server config for the Galata UI tests.

Configures a token-less Jupyter Server (so Galata/Playwright can drive it) and
points the extension at the model-free stub agent in this directory.
"""
import os

from jupyterlab.galata import configure_jupyter_server

c = get_config()  # noqa: F821 (provided by Jupyter's config machinery)

configure_jupyter_server(c)

# Drive the extension with the deterministic, no-API-key stub agent.
_stub = os.path.join(os.path.dirname(__file__), "stub_agent.py")
os.environ.setdefault("DEEPAGENT_AGENT_SPEC", f"{_stub}:graph")

# Make the failure mode obvious if the extension didn't get installed.
c.LabApp.expose_app_in_browser = True
