# UI tests (Galata)

Browser-level tests for the JupyterLab extension, driven by
[Galata](https://github.com/jupyterlab/jupyterlab/tree/main/galata) (JupyterLab's
Playwright harness). This directory is **self-contained and never shipped** — its
dependencies are not in the published wheel and add nothing to a user's install.

The tests run against `stub_agent.py`, a model-free LangGraph graph that echoes
the user's message. So they need **no API key** and are deterministic.

## Run locally

```bash
# 1. Build + install the extension into a JupyterLab env (from the repo root)
pip install -e ".[test]"        # or your usual editable install
jupyter labextension develop . --overwrite
jupyter lab build               # if needed

# 2. Install the UI-test deps and a browser
cd ui-tests
jlpm install
jlpm playwright install chromium

# 3. Run
jlpm test                       # headless
jlpm test:debug                 # headed / inspector
```

`jlpm` is the yarn wrapper shipped with JupyterLab; plain `npm`/`yarn` also work.

## What it covers

`tests/deepagent-lab.spec.ts` is a smoke test: opens the chat sidebar, waits for
the agent health indicator to go green, sends a message, and asserts the streamed
reply renders. Add visual-regression checks with `expect(page).toHaveScreenshot()`
and refresh baselines via `jlpm test:update`.
