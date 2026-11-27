"""Pytest fixtures + module-import-time subprocess-coverage wiring.

An empty conftest.py at tests/ is the canonical SciTeX convention
(audit-project PS208) — it pins the pytest rootdir and gives downstream
fixtures a home.

Subprocess coverage is configured in pyproject.toml via coverage.py's
subprocess patch; test collection must not install .pth files or
overwrite caller-owned coverage environment variables.
"""

from __future__ import annotations

import os

import pytest


# ---------------------------------------------------------------------------
# Real-collaborator fixtures (no-mocks discipline — see PA-306 / STX-NM00*).
# Replace `monkeypatch.setenv(...)` usage with this yield-based fixture so
# the production code path stays exercised (real `os.environ` read), and
# the test's mutations are snapshotted + restored honestly across teardown.
# ---------------------------------------------------------------------------
@pytest.fixture
def env_save_restore():
    """Snapshot ``os.environ`` and restore it at teardown.

    Yields a setter helper: ``env(name, value=None)``.
    ``value=None`` deletes the var (think ``monkeypatch.delenv``).
    Use this instead of `monkeypatch.setenv` / `.delenv` — the
    no-mocks rule bans the `monkeypatch` fixture entirely.
    """
    saved = dict(os.environ)

    def setter(name: str, value: str | None = None) -> None:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

    try:
        yield setter
    finally:
        # Restore in two passes: drop additions, then put back originals.
        for k in list(os.environ.keys()):
            if k not in saved:
                del os.environ[k]
        for k, v in saved.items():
            os.environ[k] = v
