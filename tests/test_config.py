from __future__ import annotations

import pytest

from purpory.config import _load_pyproject_toml


def test_missing_pyproject_has_no_project_settings(tmp_path):
    assert _load_pyproject_toml(tmp_path) == {}


def test_malformed_pyproject_is_not_treated_as_default_settings(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.purpory\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="could not load Purpory settings"):
        _load_pyproject_toml(tmp_path)


def test_purpory_settings_must_be_a_table(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        'tool = { purpory = "invalid" }\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"\[tool\.purpory\] must be a TOML table"):
        _load_pyproject_toml(tmp_path)
