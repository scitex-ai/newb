"""Regression: importing tests/conftest.py must not touch site-packages."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path


def test_conftest_leaves_site_packages_untouched():
    # Arrange
    conftest_path = Path(__file__).resolve().parents[1] / "conftest.py"
    child_code = textwrap.dedent(
        """\
        import os
        import runpy
        import site

        os.environ["COVERAGE_PROCESS_START"] = "sentinel-process-start"
        os.environ["COVERAGE_FILE"] = "sentinel-coverage-file"

        def snapshot():
            names = {}
            dirs = []
            try:
                dirs.extend(site.getsitepackages())
            except Exception:
                pass
            try:
                dirs.append(site.getusersitepackages())
            except Exception:
                pass
            for d in dirs:
                try:
                    names[d] = sorted(os.listdir(d))
                except OSError:
                    names[d] = None
            return names

        before = snapshot()
        runpy.run_path(CONFTEST, run_name="conftest_under_test")
        after = snapshot()
        assert before == after, "conftest modified site-packages"
        assert os.environ["COVERAGE_PROCESS_START"] == "sentinel-process-start"
        assert os.environ["COVERAGE_FILE"] == "sentinel-coverage-file"
        """
    ).replace("CONFTEST", repr(str(conftest_path)))

    # Act
    proc = subprocess.run(
        [sys.executable, "-c", child_code],
        capture_output=True,
        text=True,
    )

    # Assert
    assert proc.returncode == 0, proc.stderr
