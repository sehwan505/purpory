from __future__ import annotations
import os
import sys
from pathlib import Path

def _load_pyproject_toml(root_path: Path) -> dict:
    toml_path = root_path / "pyproject.toml"
    if not toml_path.exists():
        return {}

    try:
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib

        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
    except Exception as exc:
        raise RuntimeError(f"could not load Purpory settings from {toml_path}: {exc}") from exc

    tool = data.get("tool", {})
    if not isinstance(tool, dict):
        raise ValueError(f"{toml_path}: [tool] must be a TOML table")
    config = tool.get("purpory", {})
    if not isinstance(config, dict):
        raise ValueError(f"{toml_path}: [tool.purpory] must be a TOML table")
    return config

class Settings:
    def __init__(self, root_path: Path | None = None):
        if root_path is None:
            root_path = Path(".")

        toml_config = _load_pyproject_toml(root_path)

        # 1. Community Detection Settings (cluster.py)
        self.max_community_fraction = float(
            toml_config.get("max_community_fraction") or
            os.environ.get("PURPORY_MAX_COMMUNITY_FRACTION", 0.25)
        )
        self.min_split_size = int(
            toml_config.get("min_split_size") or
            os.environ.get("PURPORY_MIN_SPLIT_SIZE", 10)
        )
        self.cohesion_split_threshold = float(
            toml_config.get("cohesion_split_threshold") or
            os.environ.get("PURPORY_COHESION_SPLIT_THRESHOLD", 0.05)
        )
        self.cohesion_split_min_size = int(
            toml_config.get("cohesion_split_min_size") or
            os.environ.get("PURPORY_COHESION_SPLIT_MIN_SIZE", 50)
        )

        # 2. Token & File Limits (llm/helpers.py)
        self.file_char_cap = int(
            toml_config.get("file_char_cap") or
            os.environ.get("PURPORY_FILE_CHAR_CAP", 20000)
        )
        self.per_file_overhead_chars = int(
            toml_config.get("per_file_overhead_chars") or
            os.environ.get("PURPORY_PER_FILE_OVERHEAD_CHARS", 160)
        )
        self.chars_per_token = int(
            toml_config.get("chars_per_token") or
            os.environ.get("PURPORY_CHARS_PER_TOKEN", 4)
        )

        # 3. Extraction performance (extract.py)
        self.parallel_threshold = int(
            toml_config.get("parallel_threshold") or
            os.environ.get("PURPORY_PARALLEL_THRESHOLD", 20)
        )

# Global settings instance
settings = Settings()
