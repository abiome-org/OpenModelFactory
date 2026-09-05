from __future__ import annotations

import os
import subprocess
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def offline_environment() -> dict[str, str]:
    wheels = ROOT / ".venv" / "wheels"
    if not wheels.is_dir():
        raise RuntimeError(
            "Run make setup to prepare locked wheels for isolated installation tests."
        )
    return os.environ | {"PIP_NO_INDEX": "1", "PIP_FIND_LINKS": wheels.as_uri()}


def create_environment(path: Path) -> Path:
    venv.EnvBuilder(with_pip=True).create(path)
    python = path / "bin/python"
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--only-binary=:all:",
            "--require-hashes",
            "-r",
            str(ROOT / "requirements.lock"),
            "-r",
            str(ROOT / "requirements.build.lock"),
        ],
        env=offline_environment(),
        capture_output=True,
        text=True,
        check=True,
    )
    return python
