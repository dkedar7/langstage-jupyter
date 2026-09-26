"""The LangStage family's command-line exit codes.

Defined locally (not imported from ``langstage_core.cli``) so this package doesn't need
a newer core. The numbers are the contract, per langstage-core ADR 0007:
https://github.com/dkedar7/langstage-core/blob/main/docs/adr/0007-family-exit-codes.md

    0   success
    1   failure: no/bad agent, load error, turn error, a check failed, can't start
    2   paused on a human-in-the-loop interrupt (only this; never a failure)
    64  usage error: bad or conflicting command-line arguments
"""
from __future__ import annotations

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_PAUSED = 2
EXIT_USAGE = 64

_OUTCOME_CODES = {"complete": EXIT_OK, "error": EXIT_FAIL, "interrupted": EXIT_PAUSED}


def exit_code_for_outcome(outcome) -> int:
    """complete -> 0, interrupted -> 2, error or anything unrecognized -> 1."""
    return _OUTCOME_CODES.get(outcome, EXIT_FAIL) if isinstance(outcome, str) else EXIT_FAIL
