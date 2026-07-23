"""Bash extractor."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from purpory.extractors.base import _file_stem, _make_id, _read_text, ExtractionContext

class BashExtractor:
    _BASH_SOURCE_COMMANDS = frozenset({"source", "."})
    _BASH_SCRIPT_RUNNERS = frozenset({"bash", "sh", "zsh", "ksh", "dash"})
    # Parent node types that mean a contained command is part of a substitution
    # or expansion, not a real function call. Token-level filtering misses
    # these because `$(build)` exposes `build` as a child command whose name
    # token has no metacharacters — only the parent does.
    _BASH_EXPANSION_PARENTS = frozenset({
        "command_substitution",
        "process_substitution",
    })

    def __init__(self, ctx: ExtractionContext, source: bytes):
        self.ctx = ctx
        self.source = source
        self.function_bodies: list[tuple[str, Any]] = []
        self.defined_functions: set[str] = set()

    def text(self, node) -> str:
        return self.source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def is_inside_expansion(self, node) -> bool:
        parent = node.parent
        while parent is not None:
            if parent.type in self._BASH_EXPANSION_PARENTS:
                return True
            parent = parent.parent
        return False

    def literal(self, node) -> str | None:
        # Token-level filter: rejects names containing shell metacharacters.
        # Combined with `is_inside_expansion` for parent-context rejection.
        raw = self.text(node).strip()
        if not raw:
            return None
        if raw[0:1] in {"'", '"'} and raw[-1:] == raw[0]:
            raw = raw[1:-1]
        if any(token in raw for token in ("$", "`", "$(", "<(", ">", "|", ";", "&")):
            return None
        return raw

    def _bash_func_name(self, node) -> str | None:
        """Get the name from a function_definition node."""
        # bash grammar: function_definition has a word child (the name)
        for child in node.children:
            if child.type == "word":
                return self.literal(child)
        return None

    def walk_calls(self, body_node, func_nid: str, seen_calls: set) -> None:
        if body_node is None:
            return
        for child in body_node.children:
            if child.type == "function_definition":
                # Skip nested function definitions — their bodies are walked
                # separately, so we don't attribute their calls to the
                # enclosing scope.
                continue
            if child.type == "command" and not self.is_inside_expansion(child):
                cmd_name_node = child.child_by_field_name("name")
                if cmd_name_node is None and child.children:
                    cmd_name_node = child.children[0]
                if cmd_name_node:
                    name = self.literal(cmd_name_node)
                    # Defined-functions wins. Skip-lists for external commands
                    # would create false negatives when a user defines a
                    # function shadowing an external (`install`, `find`, etc.).
                    if name and name in self.defined_functions:
                        tgt = _make_id(self.ctx.stem, name)
                        key = (func_nid, tgt)
                        if tgt and key not in seen_calls:
                            seen_calls.add(key)
                            self.ctx.add_edge(func_nid, tgt, "calls",
                                              child.start_point[0] + 1,
                                              confidence="EXTRACTED", context="call")
            self.walk_calls(child, func_nid, seen_calls)

    def walk(self, node, parent_nid: str) -> None:
        t = node.type
        if t == "function_definition":
            name = self._bash_func_name(node)
            if name:
                fn_nid = _make_id(self.ctx.stem, name)
                line = node.start_point[0] + 1
                self.ctx.add_node(fn_nid, f"{name}()", line, kind="bash_function")
                self.ctx.add_edge(parent_nid, fn_nid, "defines", line)
                self.defined_functions.add(name)
                # find the compound_statement body
                body = None
                for child in node.children:
                    if child.type == "compound_statement":
                        body = child
                        break
                self.function_bodies.append((fn_nid, body))
                # Recurse into the body so nested function definitions are discovered
                # and added to function_bodies for the second-pass walk_calls.
                if body is not None:
                    self.walk(body, fn_nid)
            return

        if t == "command":
            if self.is_inside_expansion(node):
                return
            cmd_name_node = node.child_by_field_name("name")
            if cmd_name_node is None and node.children:
                cmd_name_node = node.children[0]
            if cmd_name_node:
                cmd = self.literal(cmd_name_node)
                args = [c for c in node.children
                        if c.type in ("word", "string", "concatenation")
                        and c != cmd_name_node]
                if cmd in self._BASH_SOURCE_COMMANDS and cmd not in self.defined_functions:
                    # find the path argument (first word after command name)
                    if args:
                        raw = _read_text(args[0], self.source).strip().strip("'\"")
                        line = node.start_point[0] + 1
                        if raw.startswith((".", "/")):
                            resolved = (self.ctx.path.parent / raw).resolve()
                            # Only emit the edge if the target actually exists on
                            # disk — prevents graph pollution from crafted paths
                            # like `source ../../etc/passwd` that traverse outside
                            # the project tree (B-1).
                            if resolved.exists():
                                tgt_nid = _make_id(str(resolved))
                                self.ctx.add_edge(self.ctx.file_nid, tgt_nid, "imports_from", line,
                                                  context="import")
                        else:
                            tgt_nid = _make_id(raw)
                            if tgt_nid:
                                self.ctx.add_edge(self.ctx.file_nid, tgt_nid, "imports", line,
                                                  context="import")
                elif cmd and cmd not in self.defined_functions:
                    raw = cmd if cmd.endswith(".sh") else None
                    if cmd in self._BASH_SCRIPT_RUNNERS and args:
                        raw = self.literal(args[0])
                    if raw and raw.endswith(".sh"):
                        resolved = (self.ctx.path.parent / raw).resolve()
                        if resolved.is_file():
                            target_path = resolved
                            if not self.ctx.path.is_absolute():
                                try:
                                    target_path = resolved.relative_to(Path.cwd().resolve())
                                except ValueError:
                                    pass
                            caller_nid = (self.ctx.file_nid + "__entry") if parent_nid == self.ctx.file_nid else parent_nid
                            self.ctx.add_edge(caller_nid, _make_id(str(target_path)) + "__entry",
                                              "calls", node.start_point[0] + 1,
                                              context="script_invocation")
            return

        if t == "declaration_command":
            # export/declare/readonly VAR=value at program level
            if node.parent and node.parent.type == "program":
                for child in node.children:
                    if child.type == "variable_assignment":
                        var_node = child.child_by_field_name("name")
                        if var_node:
                            var = _read_text(var_node, self.source).strip()
                            if var:
                                var_nid = _make_id(self.ctx.stem, var)
                                line = child.start_point[0] + 1
                                self.ctx.add_node(var_nid, var, line)
                                self.ctx.add_edge(self.ctx.file_nid, var_nid, "defines", line)
            return

        for child in node.children:
            self.walk(child, parent_nid)

    def _prescan_functions(self, node) -> None:
        if node.type == "function_definition":
            name = self._bash_func_name(node)
            if name:
                self.defined_functions.add(name)
            for child in node.children:
                self._prescan_functions(child)
        else:
            for child in node.children:
                self._prescan_functions(child)


def extract_bash(path: Path) -> dict:
    """Extract functions, source imports, and cross-function calls from a .sh file."""
    try:
        import tree_sitter_bash as tsbash
        from tree_sitter import Language, Parser
    except ImportError:
        return {"nodes": [], "edges": [], "error": "tree-sitter-bash not installed"}

    try:
        language = Language(tsbash.language())
        parser = Parser(language)
        source = path.read_bytes()
        tree = parser.parse(source)
        root = tree.root_node
    except Exception as e:
        return {"nodes": [], "edges": [], "error": str(e)}

    ctx = ExtractionContext(path, "bash")
    file_nid = ctx.file_nid
    entry_nid = file_nid + "__entry"

    ctx.add_node(file_nid, path.name, 1, kind="file")
    ctx.add_node(entry_nid, f"{path.name} script", 1, kind="bash_entrypoint")
    ctx.add_edge(file_nid, entry_nid, "contains", 1)

    extractor = BashExtractor(ctx, source)
    extractor._prescan_functions(root)
    extractor.walk(root, file_nid)

    # Second pass: cross-function calls
    top_seen = set()
    extractor.walk_calls(root, entry_nid, top_seen)  # top-level calls attributed to the entrypoint
    for fn_nid, body in extractor.function_bodies:
        extractor.walk_calls(body, fn_nid, set())

    res = ctx.get_result()
    return {"nodes": res["nodes"], "edges": res["edges"]}
