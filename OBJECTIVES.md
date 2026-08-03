# Objectives & scope — langstage-jupyter

*What this repo is for, who it serves, and what it deliberately is **not** — the yardstick
for deciding whether a proposed change or filed issue belongs here. When triaging an issue,
start here.*

## Objective

Run a langstage agent **inside JupyterLab** — a prebuilt labextension plus a Python launcher —
so the agent lives where the notebook work happens: a sidebar chat, notebook-native tools
(create / execute cells), human-in-the-loop, and headless CI preflights (`--verify`,
`--serve-check`, `/health`).

## Who it's for

A JupyterLab user who wants an agent that can read and drive their notebooks.

## In scope

- The labextension + launcher and their packaging (the labextension version gate is
  **load-bearing** — build/packaging correctness is high-value here).
- Notebook-native tools, with **correct notebook↔kernel binding** (exact-path, never substring).
- The Jupyter chat / HITL surface.
- Honest preflight and `/health` that honor the documented agent-selection config
  (`LANGSTAGE_AGENT_MODULE` / `_VARIABLE`), so a keyless custom agent isn't misreported.

## Out of scope (anti-scope)

- Becoming a standalone server, or a notebook *editor*; replacing the **langstage** web app.
- Web-surface-parity features that carry no meaning inside a notebook context.
- Logic that would also be needed by another surface — that belongs in **langstage-core**.

## How this fits the family

langstage-jupyter is the **JupyterLab surface** of the family: a thin consumer of
langstage-core. A fix that another surface would also need belongs in core, not here.

## Using this to triage

Before acting on an issue or PR: does it serve the objective above? Is it in scope or
anti-scope? Weigh its value — **security > correctness > advertised-≠-honored > DX/docs >
polish > net-new feature** — against the cost of a manual release (and the labextension
rebuild). Then **fix, defer, or decline with a reason.** Not every filed issue is worth
acting on.
