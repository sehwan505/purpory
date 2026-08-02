"""Purpory command-line entry point."""

from __future__ import annotations

import errno
import os
import sys

try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("purpory")
except Exception:
    __version__ = "unknown"

from purpory.cli import run_cli
from purpory.install import (
    _AGENTS_MD_MARKER,
    _CLAUDE_MD_MARKER,
    _always_on,
    _replace_or_append_section,
    claude_install,
    claude_uninstall,
    codex_install,
    codex_uninstall,
)

_ALWAYS_ON_ALIASES = {
    "_CLAUDE_MD_SECTION": "claude-md",
    "_AGENTS_MD_SECTION": "agents-md",
}
def __getattr__(name: str) -> str:
    base = _ALWAYS_ON_ALIASES.get(name)
    if base is not None:
        return _always_on(base)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _silence_broken_pipe() -> None:
    try:
        descriptor = os.open(os.devnull, os.O_WRONLY)
        os.dup2(descriptor, sys.stdout.fileno())
    except Exception:
        pass
    raise SystemExit(0)


def main() -> None:
    try:
        run_cli(version=__version__)
        sys.stdout.flush()
    except BrokenPipeError:
        _silence_broken_pipe()
    except OSError as exc:
        if getattr(exc, "errno", None) in (errno.EPIPE, errno.EINVAL):
            _silence_broken_pipe()
        raise


if __name__ == "__main__":
    main()
