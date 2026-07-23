"""Per-language extractors, incrementally migrated out of purpory/extract.py.

Dispatch still flows through purpory.extract (the facade re-exports every
moved name), so importing from purpory.extract keeps working unchanged.
LANGUAGE_EXTRACTORS is the registry seed; wiring dispatch through it is a
later, separate step. See MIGRATION.md for how to port another language.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from purpory.extractors.apex import extract_apex
from purpory.extractors.bash import extract_bash
from purpory.extractors.blade import extract_blade
from purpory.extractors.dart import extract_dart
from purpory.extractors.dm import extract_dm, extract_dmf, extract_dmi, extract_dmm
from purpory.extractors.elixir import extract_elixir
from purpory.extractors.fortran import extract_fortran
from purpory.extractors.go import extract_go
from purpory.extractors.json_config import extract_json
from purpory.extractors.julia import extract_julia
from purpory.extractors.markdown import extract_markdown
from purpory.extractors.objc import extract_objc
from purpory.extractors.pascal import extract_pascal
from purpory.extractors.pascal_forms import extract_delphi_form, extract_lazarus_form
from purpory.extractors.powershell import extract_powershell, extract_powershell_manifest
from purpory.extractors.razor import extract_razor
from purpory.extractors.rust import extract_rust
from purpory.extractors.sln import extract_sln
from purpory.extractors.sql import extract_sql
from purpory.extractors.terraform import extract_terraform
from purpory.extractors.verilog import extract_verilog
from purpory.extractors.zig import extract_zig

LANGUAGE_EXTRACTORS: dict[str, Callable[[Path], dict]] = {
    "apex": extract_apex,
    "bash": extract_bash,
    "blade": extract_blade,
    "dart": extract_dart,
    "delphi_form": extract_delphi_form,
    "dm": extract_dm,
    "dmf": extract_dmf,
    "dmi": extract_dmi,
    "dmm": extract_dmm,
    "elixir": extract_elixir,
    "fortran": extract_fortran,
    "go": extract_go,
    "json": extract_json,
    "julia": extract_julia,
    "lazarus_form": extract_lazarus_form,
    "markdown": extract_markdown,
    "objc": extract_objc,
    "pascal": extract_pascal,
    "powershell": extract_powershell,
    "powershell_manifest": extract_powershell_manifest,
    "razor": extract_razor,
    "rust": extract_rust,
    "sln": extract_sln,
    "sql": extract_sql,
    "terraform": extract_terraform,
    "verilog": extract_verilog,
    "zig": extract_zig,
}
