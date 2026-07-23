"""gh #82 guard: the bundled labextension version must track package.json.

The published 0.6.19 wheel bundled a stale 0.6.11 labextension because the
`hatch-jupyter-builder` `skip-if-exists` guard reused a leftover
`langstage_jupyter/labextension/` from an earlier build (the third time this
drift shipped — see #38/#48). The durable fix moved the parity assertion *into*
the build: a custom hatchling build hook (`hatch_build.py`) hard-fails any build
that would package a labextension whose version differs from `package.json`.

These tests exercise that guard's decision logic directly — including a
deliberately-stale tree, which is the exact condition that shipped three times —
so a future regression in the hook is caught here, in the fast Python suite, not
only at release time. `hatch_build.py` lives at the repo root (a build-time
module, not part of the installed package), so it is loaded by path.
"""
import importlib.util
import json
from pathlib import Path

import pytest

# tomllib is stdlib on 3.11+ (this package requires >=3.11).
import tomllib

_REPO = Path(__file__).resolve().parent.parent


def _load_hatch_build():
    """Import the repo-root build hook module by path."""
    path = _REPO / "hatch_build.py"
    spec = importlib.util.spec_from_file_location("hatch_build", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_hb = _load_hatch_build()


def _make_tree(tmp_path: Path, pkg_version: str, labext_version: str | None) -> Path:
    """Build a fake project root with a package.json and (optionally) a bundled
    labextension package.json at the given versions."""
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "langstage-jupyter", "version": pkg_version}),
        encoding="utf-8",
    )
    if labext_version is not None:
        labext = tmp_path / "langstage_jupyter" / "labextension"
        labext.mkdir(parents=True)
        (labext / "package.json").write_text(
            json.dumps({"name": "langstage-jupyter", "version": labext_version}),
            encoding="utf-8",
        )
    return tmp_path


def test_guard_raises_on_stale_bundled_labextension(tmp_path):
    """The exact gh #82 failure: a leftover 0.6.11 bundle in a 0.6.20 tree."""
    root = _make_tree(tmp_path, pkg_version="0.6.20", labext_version="0.6.11")
    with pytest.raises(_hb.LabextVersionError) as exc:
        _hb.verify_labext_version(root)
    msg = str(exc.value)
    assert "0.6.11" in msg and "0.6.20" in msg
    # The error must be actionable — it names the clean-rebuild command.
    assert "jlpm" in msg and "build:prod" in msg


def test_guard_passes_on_matching_versions(tmp_path):
    root = _make_tree(tmp_path, pkg_version="0.6.20", labext_version="0.6.20")
    assert _hb.verify_labext_version(root) == "0.6.20"


def test_guard_is_silent_when_no_labextension_built(tmp_path):
    """A clean tree (no bundled labextension) must NOT fail: the jupyter-builder
    hook rebuilds it fresh, which matches package.json by construction."""
    root = _make_tree(tmp_path, pkg_version="0.6.20", labext_version=None)
    assert _hb.verify_labext_version(root) is None


def test_repo_tree_satisfies_the_guard():
    """The real working tree must never be in the stale state the guard forbids.

    When the labextension has not been built (e.g. the CI test job has no Node),
    the guard returns None and this passes. When it *has* been built, its version
    must equal package.json — a live parity check on the actual repo."""
    _hb.verify_labext_version(_REPO)  # must not raise


def test_package_json_is_the_single_version_source():
    """hatch derives the wheel version from package.json (hatch-nodejs-version),
    so the guard comparing against package.json is comparing against the wheel
    version. Pin that wiring so a switch away from it doesn't silently defeat the
    guard."""
    pyproject = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["tool"]["hatch"]["version"]["source"] == "nodejs"
    # And the custom guard hook is actually registered.
    assert "custom" in pyproject["tool"]["hatch"]["build"]["hooks"]
