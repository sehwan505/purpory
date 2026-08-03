"""Claude Code and Codex integration installers.

Purpory's supported automatic context integrations are intentionally limited to
the two hosts that expose a prompt-level ``UserPromptSubmit`` lifecycle.  Both
installers call the same ``purpory preflight`` adapter and preserve unrelated
instructions and hooks owned by the user.
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any


_CLAUDE_MD_MARKER = "## purpory"
_AGENTS_MD_MARKER = "## purpory"
_SUPPORTED_AGENTS = ("claude", "codex")
_RECONCILE_SKILL = "purpory-reconcile"


def _user_config_dir(agent: str) -> Path:
    variable = "CLAUDE_CONFIG_DIR" if agent == "claude" else "CODEX_HOME"
    default = ".claude" if agent == "claude" else ".codex"
    return Path(os.environ.get(variable, Path.home() / default)).expanduser()


@functools.lru_cache(maxsize=None)
def _always_on(basename: str) -> str:
    """Read a packaged project-instruction block on demand."""
    path = Path(__file__).parent / "always_on" / f"{basename}.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            f"purpory integration assets are incomplete: missing '{basename}' "
            f"at {path}. Reinstall purpory."
        ) from exc


def _install_reconcile_skill(base_dir: Path, agent: str, *, project: bool) -> None:
    source = Path(__file__).parent / "skills" / _RECONCILE_SKILL
    if not (source / "SKILL.md").is_file():
        raise RuntimeError(
            f"purpory integration assets are incomplete: missing '{_RECONCILE_SKILL}' skill"
        )
    parent = (
        base_dir / (".claude" if agent == "claude" else ".agents") / "skills"
        if project
        else base_dir / "skills"
    )
    target = parent / _RECONCILE_SKILL
    shutil.copytree(source, target, dirs_exist_ok=True)
    print(f"  {target}  ->  durable intent reconciliation skill")


def _uninstall_reconcile_skill(base_dir: Path, agent: str, *, project: bool) -> bool:
    parent = (
        base_dir / (".claude" if agent == "claude" else ".agents") / "skills"
        if project
        else base_dir / "skills"
    )
    target = parent / _RECONCILE_SKILL
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    for directory in (parent, parent.parent):
        try:
            directory.rmdir()
        except OSError:
            break
    print(f"  {target}  ->  reconciliation skill removed")
    return True


def _replace_or_append_section(content: str, marker: str, new_section: str) -> str:
    """Idempotently replace Purpory's H2 section without touching user text."""
    lines = content.split("\n")
    starts = [i for i, line in enumerate(lines) if line.strip() == marker]
    if not starts:
        if content.strip():
            return content.rstrip() + "\n\n" + new_section.lstrip()
        return new_section.lstrip()

    start = starts[-1]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith(("# ", "## ")):
            end = index
            break

    parts = []
    head = "\n".join(lines[:start]).rstrip()
    tail = "\n".join(lines[end:]).lstrip()
    if head:
        parts.append(head)
    parts.append(new_section.strip())
    if tail:
        parts.append(tail)
    return "\n\n".join(parts).rstrip() + "\n"


def _strip_purpory_md_section(target: Path, marker: str) -> bool:
    """Remove Purpory's section while preserving all surrounding content."""
    if not target.exists():
        return False
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if marker not in content:
        return False

    cleaned = re.sub(
        rf"\n*{re.escape(marker)}\n.*?(?=\n#{{1,2}} |\Z)",
        "",
        content,
        flags=re.DOTALL,
    ).rstrip()
    if cleaned:
        target.write_text(cleaned + "\n", encoding="utf-8")
    else:
        target.unlink()
    return True


def _load_json_like(path: Path) -> dict[str, Any]:
    """Load a JSON hook file, tolerating a missing or malformed document."""
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _resolve_purpory_exe() -> str:
    """Resolve the executable used by project hooks."""
    found = shutil.which("purpory")
    if found:
        return found
    scripts_dir = Path(sys.executable).parent
    for name in ("purpory.exe", "purpory"):
        candidate = scripts_dir / name
        if candidate.exists():
            return str(candidate)
    return "purpory"


def _preflight_hook(agent: str) -> dict[str, object]:
    executable = _resolve_purpory_exe()
    quoted = f'"{executable}"' if " " in executable else executable
    command = f"{quoted} preflight {agent}"
    return {
        "hooks": [
            {
                "type": "command",
                "command": command,
                "commandWindows": command,
                "timeout": 330,
                "statusMessage": "Purpory is preparing context",
            }
        ]
    }


def _hook_registry(settings: dict[str, Any]) -> dict[str, Any]:
    hooks = settings.get("hooks")
    if isinstance(hooks, dict):
        return hooks
    hooks = {}
    settings["hooks"] = hooks
    return hooks


def _is_purpory_hook(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    commands = entry.get("hooks")
    if not isinstance(commands, list):
        return False
    for command in commands:
        if not isinstance(command, dict):
            continue
        command_text = " ".join(
            str(command.get(key, "")) for key in ("command", "commandWindows")
        ).lower()
        if "purpory" not in command_text:
            continue
        if any(
            signature in command_text
            for signature in (
                "preflight claude",
                "preflight codex",
                "hook-check",
                "hook-guard",
            )
        ):
            return True
    return False


def _remove_purpory_hooks(hooks: dict[str, Any]) -> bool:
    changed = False
    for event in tuple(hooks):
        entries = hooks.get(event)
        if not isinstance(entries, list):
            continue
        retained = [entry for entry in entries if not _is_purpory_hook(entry)]
        if len(retained) == len(entries):
            continue
        changed = True
        if retained:
            hooks[event] = retained
        else:
            hooks.pop(event)
    return changed


def _install_prompt_hook(base_dir: Path, agent: str, *, project: bool) -> None:
    if agent == "claude":
        hooks_path = base_dir / (Path(".claude") / "settings.json" if project else "settings.json")
    elif agent == "codex":
        hooks_path = base_dir / (Path(".codex") / "hooks.json" if project else "hooks.json")
    else:  # pragma: no cover - internal invariant
        raise ValueError(f"unsupported agent: {agent}")

    hooks_path.parent.mkdir(parents=True, exist_ok=True)
    settings = _load_json_like(hooks_path)
    hooks = _hook_registry(settings)
    _remove_purpory_hooks(hooks)
    hooks.setdefault("UserPromptSubmit", []).append(_preflight_hook(agent))
    hooks_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print(f"  {hooks_path}  ->  UserPromptSubmit preflight registered")


def _uninstall_prompt_hook(path: Path) -> bool:
    if not path.exists():
        return False
    settings = _load_json_like(path)
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict) or not _remove_purpory_hooks(hooks):
        return False
    if not hooks:
        settings.pop("hooks", None)
    if settings:
        path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    else:
        path.unlink()
        try:
            path.parent.rmdir()
        except OSError:
            pass
    return True


def _install_claude_hook(base_dir: Path, *, project: bool) -> None:
    _install_prompt_hook(base_dir, "claude", project=project)


def _uninstall_claude_hook(base_dir: Path, *, project: bool) -> None:
    parent = base_dir / ".claude" if project else base_dir
    names = ("settings.json", "settings.local.json") if project else ("settings.json",)
    for name in names:
        path = parent / name
        if _uninstall_prompt_hook(path):
            print(f"  {path}  ->  Purpory preflight removed")


def _install_codex_hook(base_dir: Path, *, project: bool) -> None:
    _install_prompt_hook(base_dir, "codex", project=project)


def _uninstall_codex_hook(base_dir: Path, *, project: bool) -> None:
    path = base_dir / ".codex" / "hooks.json" if project else base_dir / "hooks.json"
    if _uninstall_prompt_hook(path):
        print(f"  {path}  ->  Purpory preflight removed")


def _write_instructions(target: Path, marker: str, section: str) -> None:
    current = target.read_text(encoding="utf-8") if target.exists() else ""
    updated = _replace_or_append_section(current, marker, section)
    if target.exists() and updated == current:
        print(f"purpory already configured in {target.resolve()} (no change)")
        return
    target.write_text(updated, encoding="utf-8")
    print(f"purpory section written to {target.resolve()}")


def claude_install(project_dir: Path | None = None) -> None:
    """Install Claude Code instructions and mandatory prompt preflight."""
    project = project_dir is not None
    base_dir = project_dir or _user_config_dir("claude")
    base_dir.mkdir(parents=True, exist_ok=True)
    _write_instructions(
        base_dir / "CLAUDE.md",
        _CLAUDE_MD_MARKER,
        _always_on("claude-md"),
    )
    _install_reconcile_skill(base_dir, "claude", project=project)
    _install_claude_hook(base_dir, project=project)
    print("\nClaude Code will now run Purpory before every user prompt.")


def claude_uninstall(project_dir: Path | None = None, *, project: bool = False) -> None:
    """Remove only Purpory-owned Claude Code instructions and hooks."""
    if project and project_dir is None:
        project_dir = Path(".")
    project = project_dir is not None
    base_dir = project_dir or _user_config_dir("claude")
    removed = False
    relatives = (
        (
            Path("CLAUDE.md"),
            Path("CLAUDE.local.md"),
            Path(".claude") / "CLAUDE.md",
            Path(".claude") / "CLAUDE.local.md",
        )
        if project
        else (Path("CLAUDE.md"),)
    )
    for relative in relatives:
        target = base_dir / relative
        if _strip_purpory_md_section(target, _CLAUDE_MD_MARKER):
            print(f"purpory section removed from {target.resolve()}")
            removed = True
    _uninstall_claude_hook(base_dir, project=project)
    removed = _uninstall_reconcile_skill(base_dir, "claude", project=project) or removed
    if not removed:
        print("No Claude Code Purpory instructions found - nothing to do")


def codex_install(project_dir: Path | None = None) -> None:
    """Install Codex instructions and mandatory prompt preflight."""
    project = project_dir is not None
    base_dir = project_dir or _user_config_dir("codex")
    base_dir.mkdir(parents=True, exist_ok=True)
    _write_instructions(
        base_dir / "AGENTS.md",
        _AGENTS_MD_MARKER,
        _always_on("agents-md"),
    )
    _install_reconcile_skill(base_dir, "codex", project=project)
    _install_codex_hook(base_dir, project=project)
    print("\nCodex will now run Purpory before every user prompt.")


def codex_uninstall(project_dir: Path | None = None) -> None:
    """Remove only Purpory-owned Codex instructions and hooks."""
    project = project_dir is not None
    base_dir = project_dir or _user_config_dir("codex")
    target = base_dir / "AGENTS.md"
    removed = _strip_purpory_md_section(target, _AGENTS_MD_MARKER)
    if removed:
        print(f"purpory section removed from {target.resolve()}")
    _uninstall_codex_hook(base_dir, project=project)
    removed = _uninstall_reconcile_skill(base_dir, "codex", project=project) or removed
    if not removed:
        print("No Codex Purpory instructions found - nothing to do")


def _parse_named_agent_options(args: list[str]) -> bool:
    """Return whether the integration should be installed in the current project."""
    unknown = [arg for arg in args if arg != "--project"]
    if unknown:
        print(f"error: unknown integration option '{unknown[0]}'", file=sys.stderr)
        raise SystemExit(2)
    return "--project" in args


def _dispatch_agent(agent: str, arguments: list[str]) -> None:
    subcommand = arguments[0] if arguments else ""
    project = _parse_named_agent_options(arguments[1:])
    target = Path(".") if project else None
    if subcommand == "install":
        (claude_install if agent == "claude" else codex_install)(target)
        return
    if subcommand == "uninstall":
        (claude_uninstall if agent == "claude" else codex_uninstall)(target)
        return
    print(f"Usage: purpory {agent} [install|uninstall] [--project]", file=sys.stderr)
    raise SystemExit(1)


def dispatch_install_cli(command: str, arguments: list[str] | None = None) -> bool:
    """Dispatch the two supported agent integration command groups."""
    if command not in _SUPPORTED_AGENTS:
        return False
    _dispatch_agent(command, arguments or [])
    return True
