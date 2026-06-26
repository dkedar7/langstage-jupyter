"""`.env` is resolved from the launch cwd, not the installed package (gh #32).

A bare `load_dotenv()` searches upward from the calling module — inside
site-packages once installed — so the user's project `.env` was never found and
silently ignored. The loader must use `find_dotenv(usecwd=True)`.
"""

import subprocess
import sys


def test_dotenv_resolved_from_launch_cwd(tmp_path):
    (tmp_path / ".env").write_text("SENTINEL_FROM_DOTENV=from_env_file\n")
    # Fresh process whose cwd contains the .env; importing the package runs the
    # module-level load_dotenv. With the fix it finds tmp_path/.env.
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import langstage_jupyter.agent_wrapper; import os; "
            "print(os.environ.get('SENTINEL_FROM_DOTENV', 'MISSING'))",
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "from_env_file" in r.stdout, f"stdout={r.stdout!r} stderr={r.stderr!r}"
