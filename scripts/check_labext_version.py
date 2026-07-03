#!/usr/bin/env python
"""Assert a built wheel's bundled labextension version == the wheel version.

Why this exists (gh #38, gh #48 — the *same* drift, twice): the
`hatch-jupyter-builder` hook has `skip-if-exists`, so if a previously-built
`langstage_jupyter/labextension/` is present, the npm rebuild is SKIPPED and the
wheel ships the *previous* release's JS bundle — `jupyter labextension list`
then reports the old version while `pip`/`--version` report the new one. It
passed CI both times (a clean checkout has no stale artifact to skip over), so
the only place it bites is a local release build. This check is the release-time
gate: run it against `dist/*.whl` *before* `uv publish`.

Usage:
    python scripts/check_labext_version.py [wheel ...]   # defaults to dist/*.whl

Exit code 0 = all wheels match; 1 = a mismatch (or a wheel with no bundled
labextension); 2 = nothing to check.
"""

from __future__ import annotations

import glob
import json
import re
import sys
import zipfile
from pathlib import Path

# The bundled labextension manifest, wherever hatchling's shared-data mapping put
# it inside the wheel (…/share/jupyter/labextensions/langstage-jupyter/package.json).
_LABEXT_MANIFEST = "labextensions/langstage-jupyter/package.json"


def _wheel_version(wheel_path: str) -> str | None:
    # dist/langstage_jupyter-0.6.2-py3-none-any.whl -> 0.6.2
    m = re.match(r"[^-]+-([^-]+)-", Path(wheel_path).name)
    return m.group(1) if m else None


def main(argv: list[str]) -> int:
    wheels = argv or sorted(glob.glob("dist/*.whl"))
    if not wheels:
        print("check_labext_version: no wheel found (build one first)", file=sys.stderr)
        return 2

    failures = 0
    for wheel in wheels:
        wheel_v = _wheel_version(wheel)
        with zipfile.ZipFile(wheel) as zf:
            manifests = [n for n in zf.namelist() if n.endswith(_LABEXT_MANIFEST)]
            if not manifests:
                print(f"MISMATCH {wheel}: no bundled labextension package.json")
                failures += 1
                continue
            labext_v = json.loads(zf.read(manifests[0]))["version"]
        if labext_v != wheel_v:
            print(
                f"MISMATCH {wheel}: wheel version {wheel_v!r} != bundled labextension "
                f"version {labext_v!r} -- rebuild the labextension cleanly "
                f"(rm -rf langstage_jupyter/labextension && jlpm build:prod) before publishing."
            )
            failures += 1
        else:
            print(f"ok {Path(wheel).name}: wheel == labextension == {wheel_v}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
