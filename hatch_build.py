"""Build-time guard: the bundled labextension version MUST equal package.json.

Why this exists (gh #82 — the *third* occurrence of #38/#48): the
``hatch-jupyter-builder`` hook is configured with ``skip-if-exists``
(``langstage_jupyter/labextension/static/style.js``). So a real release build --
``uv build`` run against a working tree that still holds a *previous* release's
``langstage_jupyter/labextension/`` -- reuses that stale JS bundle instead of
rebuilding it. ``jupyter labextension list`` then reports the old version while
``pip`` / ``--version`` / the server extension report the new one (an
advertised-vs-honored mismatch). The directory is git-ignored, so the drift never
shows up in ``git status`` and a clean CI checkout has no stale artifact to reuse
-- the failure is *local to the maintainer's release machine*, which is exactly
why it slipped through three times.

``scripts/check_labext_version.py`` was added to catch this against a built
wheel, but it has to be run *by hand* before ``uv publish`` and simply wasn't
before 0.6.19 shipped. A safeguard a human can forget is not a safeguard. This
hook moves the *same* assertion INTO the build itself: it runs on every
``uv build`` / ``pip wheel .`` / editable install and raises, so a wheel that
would bundle a stale labextension can never be produced -- the build fails loudly
instead of silently packaging the wrong frontend. No manual step to remember.

The check is deliberately narrow and order-independent: it only fires when a
built ``langstage_jupyter/labextension/`` is present *and* its version differs
from ``package.json``. When the directory is absent (a clean tree) the
jupyter-builder hook rebuilds it fresh -- which matches ``package.json`` by
construction -- so there is nothing to assert and this hook stays silent.
"""

from __future__ import annotations

import json
from pathlib import Path

try:
    from hatchling.builders.hooks.plugin.interface import BuildHookInterface
except ModuleNotFoundError:  # pragma: no cover
    # hatchling is a *build* backend, not a runtime/test dependency. Keep the
    # parity logic below importable (the regression test imports it) even where
    # the build backend isn't installed; the hook class is only needed when
    # hatchling actually drives a build, and hatchling is present by definition
    # then.
    BuildHookInterface = None

_LABEXT_MANIFEST = ("langstage_jupyter", "labextension", "package.json")


class LabextVersionError(Exception):
    """Raised when the bundled labextension version drifts from package.json."""


def _read_version(manifest: Path) -> str:
    return json.loads(manifest.read_text(encoding="utf-8"))["version"]


def verify_labext_version(root: str | Path) -> str | None:
    """Assert a bundled labextension's version matches ``package.json``.

    ``root`` is the project root (the directory holding ``package.json``).

    Returns the agreed version string when a bundled labextension is present and
    matches; returns ``None`` when no bundled labextension is present yet (a
    clean tree -- the jupyter-builder hook will build it fresh, which matches by
    construction). Raises :class:`LabextVersionError` when a bundled
    labextension is present but its version differs from ``package.json`` -- the
    gh #82 stale-reuse condition that ``skip-if-exists`` would otherwise package.
    """
    root = Path(root)
    pkg_version = _read_version(root / "package.json")
    labext_manifest = root.joinpath(*_LABEXT_MANIFEST)
    if not labext_manifest.exists():
        # Nothing built yet; the jupyter-builder hook produces a fresh bundle
        # (== package.json). Only a *reused* bundle can be stale.
        return None
    labext_version = _read_version(labext_manifest)
    if labext_version != pkg_version:
        raise LabextVersionError(
            f"bundled labextension version {labext_version!r} != package.json "
            f"version {pkg_version!r}. A stale langstage_jupyter/labextension/ is "
            f"about to be packaged -- the hatch-jupyter-builder skip-if-exists "
            f"guard reused a previous release's JS bundle instead of rebuilding "
            f"it (gh #82; third recurrence of #38/#48). Rebuild the labextension "
            f"cleanly, then re-run the build:\n"
            f"    jlpm clean:labextension && jlpm build:prod"
        )
    return pkg_version


if BuildHookInterface is not None:

    class LabextVersionGuard(BuildHookInterface):
        """Fail the build if the bundled labextension version != package.json."""

        PLUGIN_NAME = "labext-version-guard"

        def initialize(self, version, build_data):  # noqa: ARG002 - hatch signature
            verify_labext_version(self.root)
