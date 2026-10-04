"""Integration tests for installer entry points and offline onboarding smoke paths."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import os
from pathlib import Path


def test_installer_help_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ackstreet.installer.cli", "--help"],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "--provider" in result.stdout
    assert "--non-interactive" in result.stdout


def test_offline_openrouter_smoke_non_interactive() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ)
        env["ACKSTREET_HOME"] = tmp
        env["ACKSTREET_SKIP_ONBOARDING"] = "0"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ackstreet.installer.cli",
                "--non-interactive",
                "--provider",
                "openrouter",
                "--model",
                "qwen/qwen3-32b:free",
                "--api-key",
                "sk-or-test",
                "--gateway",
                "none",
                "--skip-doctor",
            ],
            text=True,
            capture_output=True,
            timeout=30,
            env=env,
        )

        assert result.returncode == 0, result.stderr
        cfg = Path(tmp) / "config.toml"
        env_file = Path(tmp) / ".env"
        assert cfg.exists()
        assert env_file.exists()
        assert "https://openrouter.ai/api/v1" in cfg.read_text(encoding="utf-8")
        assert "sk-or-test" in env_file.read_text(encoding="utf-8")
