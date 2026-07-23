"""Facade / registry identity guards for the per-language extractor split (#1212).

The ``extract.py`` decomposition (#1737) moved each language extractor into its
own ``purpory/extractors/<lang>.py`` module, kept a verbatim re-export in
``purpory.extract`` (the facade every existing importer uses), and seeded a
``purpory.extractors.LANGUAGE_EXTRACTORS`` registry. Three things must stay
true for that split to be behavior-preserving:

- the function is importable from its new per-language module,
- ``purpory.extract`` still re-exports the SAME function object (facade
  identity — a stale copy or shadowing import would silently diverge),
- ``LANGUAGE_EXTRACTORS`` maps to that same object (registry identity).

Originally proposed by @Cekaru in #1721 as a per-language check; generalized
here to sweep the whole registry so a future move that forgets the facade
re-export (or re-exports a different object) fails loudly.
"""
from __future__ import annotations

import purpory.extract as facade
from purpory.extractors import LANGUAGE_EXTRACTORS


def test_every_registry_extractor_is_reexported_from_facade():
    missing = []
    diverged = []
    for lang, fn in LANGUAGE_EXTRACTORS.items():
        name = getattr(fn, "__name__", None)
        if not name or not hasattr(facade, name):
            missing.append((lang, name))
            continue
        if getattr(facade, name) is not fn:
            diverged.append((lang, name))
    assert not missing, f"registry extractors not re-exported from purpory.extract: {missing}"
    assert not diverged, f"facade object diverges from registry: {diverged}"


def test_terraform_migrated():
    # The concrete anchor from #1721: extract_terraform lives in its own module,
    # and both the facade and the registry point at that one object.
    from purpory.extractors.terraform import extract_terraform

    assert facade.extract_terraform is extract_terraform
    assert LANGUAGE_EXTRACTORS["terraform"] is extract_terraform
