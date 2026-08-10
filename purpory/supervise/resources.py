"""Generic resource discovery with Git as the first provider.

The context core stores projects, resources, and resource views without
depending on Git concepts. This module is the provider adapter that translates
repositories and worktrees into those generic records.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


GIT_REMOTE_SCHEMES = frozenset({"http", "https", "ssh", "git"})


def normalize_git_remote(value: str) -> str | None:
    """Return a provider-neutral remote identity for URL and SCP-style remotes."""
    remote = value.strip()
    scp = re.fullmatch(r"(?:[^@\s]+@)?([^:\s]+):(.+)", remote)
    if scp is not None and "://" not in remote:
        host = scp.group(1).lower()
        path = scp.group(2)
        if len(host) == 1 and path.startswith(("/", "\\")):
            return None
    else:
        parsed = urlsplit(remote)
        if parsed.scheme.lower() not in GIT_REMOTE_SCHEMES or not parsed.hostname:
            return None
        host = parsed.hostname.lower()
        if parsed.port:
            host = f"{host}:{parsed.port}"
        path = parsed.path
    normalized_path = path.strip("/")
    if normalized_path.endswith(".git"):
        normalized_path = normalized_path[:-4]
    if not normalized_path:
        return None
    return f"{host}/{normalized_path}"


def describe_git_remote(value: str) -> dict[str, Any]:
    identity = normalize_git_remote(value)
    if identity is None:
        raise ValueError(f"not a supported Git remote URL: {value}")
    return {
        "provider": "git",
        "resourceKind": "repository",
        "externalIdentity": identity,
        "resourceLabel": identity.rsplit("/", 1)[-1],
        "resourceProperties": {"remoteUrl": identity},
        "views": [],
    }


def _git(
    path: Path,
    *arguments: str,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess[Any]:
    git_executable = shutil.which("git")
    if git_executable is None:
        raise OSError("git executable is not available")
    return subprocess.run(  # noqa: S603
        [git_executable, "-C", str(path), *arguments],
        check=check,
        capture_output=True,
        text=text,
        timeout=10,
    )


def _required_git_text(path: Path, *arguments: str) -> str:
    try:
        result = _git(path, *arguments)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"path is not a usable Git checkout: {path}") from exc
    value = str(result.stdout).strip()
    if not value:
        raise ValueError(f"Git did not return {' '.join(arguments)} for {path}")
    return value


def _optional_git_text(path: Path, *arguments: str) -> str | None:
    try:
        result = _git(path, *arguments, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = str(result.stdout).strip()
    return value or None


def _absolute_git_path(value: str, *, worktree: Path) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = worktree / candidate
    return candidate.resolve()


def _state_hash(worktree: Path, revision: str | None) -> tuple[str, bool]:
    try:
        status = _git(
            worktree,
            "status",
            "--porcelain=v1",
            "-z",
            check=False,
            text=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        status = b""
    raw_status = bytes(status or b"")
    digest = hashlib.sha256((revision or "").encode("utf-8") + b"\0" + raw_status).hexdigest()
    return digest, bool(raw_status)


def discover_git_worktree(path: str | Path) -> dict[str, Any]:
    """Describe one Git worktree as a generic resource view."""
    requested = Path(path).expanduser().resolve()
    if not requested.is_dir():
        raise ValueError(f"resource path must be an existing directory: {requested}")
    worktree = Path(_required_git_text(requested, "rev-parse", "--show-toplevel")).resolve()
    common_git_dir = _absolute_git_path(
        _required_git_text(worktree, "rev-parse", "--git-common-dir"),
        worktree=worktree,
    )
    git_dir = _absolute_git_path(
        _required_git_text(worktree, "rev-parse", "--git-dir"),
        worktree=worktree,
    )
    revision = _optional_git_text(worktree, "rev-parse", "--verify", "HEAD")
    branch = _optional_git_text(worktree, "symbolic-ref", "--quiet", "--short", "HEAD")
    remote_url = _optional_git_text(worktree, "remote", "get-url", "origin")
    remote_identity = normalize_git_remote(remote_url or "")
    state_hash, dirty = _state_hash(worktree, revision)
    return {
        "provider": "git",
        "resourceKind": "repository",
        "externalIdentity": remote_identity or str(common_git_dir),
        "resourceLabel": common_git_dir.parent.name if common_git_dir.name == ".git" else worktree.name,
        "resourceProperties": {
            "commonGitDir": str(common_git_dir),
            **({"remoteUrl": remote_identity} if remote_identity else {}),
        },
        "view": {
            "locator": str(worktree),
            "revision": revision,
            "stateHash": state_hash,
            "properties": {
                "branch": branch,
                "dirty": dirty,
                "gitDir": str(git_dir),
            },
        },
    }


def discover_git_resource(path: str | Path) -> dict[str, Any]:
    """Discover a repository and every locally available worktree."""
    if isinstance(path, str) and normalize_git_remote(path) is not None:
        return describe_git_remote(path)
    primary = discover_git_worktree(path)
    primary_path = Path(primary["view"]["locator"])
    candidates: list[Path] = [primary_path]
    try:
        listing = _git(primary_path, "worktree", "list", "--porcelain").stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        listing = ""
    for line in str(listing).splitlines():
        if not line.startswith("worktree "):
            continue
        candidate = Path(line.removeprefix("worktree ")).expanduser()
        if candidate.is_dir():
            candidates.append(candidate.resolve())

    views: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            discovered = discover_git_worktree(candidate)
        except ValueError:
            continue
        locator = str(discovered["view"]["locator"])
        if locator in seen or discovered["externalIdentity"] != primary["externalIdentity"]:
            continue
        seen.add(locator)
        views.append(discovered["view"])
    views.sort(key=lambda item: str(item["locator"]))
    return {
        **{key: value for key, value in primary.items() if key != "view"},
        "primaryViewLocator": str(primary_path),
        "views": views,
    }
