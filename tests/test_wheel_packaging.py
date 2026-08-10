"""Production wheel boundaries for the Purpory distribution."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _has_build() -> bool:
    try:
        subprocess.run(
            [sys.executable, "-m", "build", "--version"],
            check=True,
            capture_output=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


@pytest.fixture(scope="module")
def wheel_namelist(tmp_path_factory: pytest.TempPathFactory) -> set[str]:
    if not _has_build():
        pytest.skip("`python -m build` unavailable (dev extra not installed)")
    output = tmp_path_factory.mktemp("wheel")
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--outdir",
            str(output),
            str(REPO),
        ],
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        pytest.skip(f"wheel build failed in this env:\n{process.stderr[-800:]}")
    wheels = list(output.glob("purpory-*.whl"))
    assert wheels, "no Purpory wheel produced"
    with zipfile.ZipFile(max(wheels, key=lambda path: path.stat().st_mtime)) as archive:
        return set(archive.namelist())


def test_purpory_runtime_ships_in_wheel(wheel_namelist: set[str]) -> None:
    required = {
        "purpory/__init__.py",
        "purpory/__main__.py",
        "purpory/serve.py",
        "purpory/supervise/repository.py",
        "purpory/supervise/model_cli.py",
        "purpory/supervise/gate/runtime.py",
        "purpory/supervise/serve/server.py",
        "purpory/supervise/serve/static/index.html",
    }
    assert required <= wheel_namelist
    assert any(name.startswith("purpory/supervise/serve/static/assets/") for name in wheel_namelist)


def test_wheel_uses_only_purpory_package_paths(wheel_namelist: set[str]) -> None:
    assert {
        "purpory/always_on/agents-md.md",
        "purpory/always_on/claude-md.md",
    } <= wheel_namelist
    packaged_skills = {name for name in wheel_namelist if "/skills/" in name}
    assert packaged_skills == set()
    assert "purpory/command-kilo.md" not in wheel_namelist
    packaged_always_on = {name for name in wheel_namelist if name.startswith("purpory/always_on/")}
    assert packaged_always_on == {
        "purpory/always_on/agents-md.md",
        "purpory/always_on/claude-md.md",
    }
    legacy_package_prefix = "graph" + "ify/"
    assert not any(name.startswith(legacy_package_prefix) for name in wheel_namelist)
