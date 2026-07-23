"""Purpory code intelligence and context control plane."""

from importlib.metadata import PackageNotFoundError, version

from purpory.supervise import ContextService

try:
    __version__ = version("purpory")
except PackageNotFoundError:
    __version__ = "0.1.0"

__all__ = ["ContextService", "__version__"]


def __getattr__(name):
    # Keep the public library surface lazy so lightweight CLI commands start quickly.
    _map = {
        "extract": ("purpory.extract", "extract"),
        "collect_files": ("purpory.extract", "collect_files"),
        "build_from_json": ("purpory.build", "build_from_json"),
        "cluster": ("purpory.cluster", "cluster"),
        "score_all": ("purpory.cluster", "score_all"),
        "cohesion_score": ("purpory.cluster", "cohesion_score"),
        "god_nodes": ("purpory.analyze", "god_nodes"),
        "surprising_connections": ("purpory.analyze", "surprising_connections"),
        "suggest_questions": ("purpory.analyze", "suggest_questions"),
        "generate": ("purpory.report", "generate"),
        "to_json": ("purpory.export", "to_json"),
        "to_html": ("purpory.export", "to_html"),
        "to_svg": ("purpory.export", "to_svg"),
        "to_canvas": ("purpory.export", "to_canvas"),
        "to_wiki": ("purpory.wiki", "to_wiki"),
        "reflect": ("purpory.reflect", "reflect"),
        "save_query_result": ("purpory.ingest", "save_query_result"),
    }
    if name in _map:
        import importlib

        mod_name, attr = _map[name]
        mod = importlib.import_module(mod_name)
        return getattr(mod, attr)
    raise AttributeError(f"module 'purpory' has no attribute {name!r}")
