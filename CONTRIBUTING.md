# Contributing to langstage-jupyter

Thanks for helping. This repo is two things in one package: a Python Jupyter
server extension (`langstage_jupyter/`) and a TypeScript JupyterLab frontend
extension (`src/`). Issues and pull requests are welcome at
<https://github.com/dkedar7/langstage-jupyter>.

## Set up a development install

You need Python 3.11+ and Node.js 20.

```bash
git clone https://github.com/dkedar7/langstage-jupyter.git
cd langstage-jupyter
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install "jupyterlab>=4.0,<5"
pip install -e ".[dev]"                 # also builds the frontend (needs Node)
jupyter labextension develop . --overwrite
jlpm build                              # rebuild the frontend after TypeScript changes
```

Then run it keyless with the built-in demo agent:

```bash
langstage-jupyter --demo
```

For frontend work, `jlpm watch` in one terminal rebuilds on save; refresh the
browser to pick up changes. For server-side changes, restart JupyterLab. For
changes to your agent module, the sidebar's **Reload** button is enough.

## Tests

```bash
pytest tests/                            # Python: server extension, launcher, config
```

The Playwright UI tests live in `ui-tests/` and run in CI (`.github/workflows/ui-tests.yml`):

```bash
cd ui-tests && jlpm install && jlpm playwright install chromium && jlpm test
```

## Pull requests

- Keep a change focused, and add or update a test with it. A bug fix should come
  with a test that fails without the fix.
- If you change behavior a user can see (a flag, an env var, the sidebar), update
  `README.md` and add a line to `CHANGELOG.md`.
- Configuration follows the family-wide chain in
  [langstage-core](https://github.com/dkedar7/langstage-core): new settings belong
  in `langstage.toml` / `LANGSTAGE_*` env vars, not ad-hoc options.
