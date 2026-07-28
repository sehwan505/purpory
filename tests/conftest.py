from __future__ import annotations

from typing import Any

import pytest

_ANALYZE_WARNING_FILTERS = (
    "ignore:Tensorflow not installed; ParametricUMAP will be unavailable:ImportWarning:umap",
    "ignore:Please import `random` from the `scipy\\.sparse` namespace.*:"
    "DeprecationWarning:hyppo\\.independence\\.hhg",
    "ignore:The keyword argument 'nopython=False' was supplied.*:Warning:numba\\.core\\.decorators",
)


@pytest.fixture(autouse=True)
def isolate_context_database(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PURPORY_CONTEXT_DB", str(tmp_path / "context.db"))
    monkeypatch.setenv("PURPORY_STATE_DIR", str(tmp_path / "state"))


def pytest_collection_modifyitems(items: list[Any]) -> None:
    for item in items:
        if item.path.name != "test_analyze.py":
            continue
        for warning_filter in _ANALYZE_WARNING_FILTERS:
            item.add_marker(pytest.mark.filterwarnings(warning_filter))
