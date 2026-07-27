"""Deterministic recall derived from delivery and environment ledgers."""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, Iterable

from purpory.supervise.repository import ContextGraphRepository

SECONDS_PER_DAY = 86_400
DEFAULT_HALF_LIFE_DAYS = 30.0


def _elapsed_days(timestamp: int, now: int) -> float:
    return max(0.0, (now - int(timestamp)) / SECONDS_PER_DAY)


def activation(
    repository: ContextGraphRepository,
    *,
    now: int | None = None,
) -> list[dict[str, Any]]:
    current = int(time.time()) if now is None else int(now)
    with repository.connect() as connection:
        rows = connection.execute(
            """
            SELECT delivery.key, delivery.delivered_at
            FROM deliveries delivery
            WHERE EXISTS (
                SELECT 1 FROM context_nodes topic
                WHERE topic.namespace = 'memory'
                  AND topic.stable_key = delivery.key
                  AND topic.project IN ('', COALESCE(delivery.project, ''))
            )
            ORDER BY delivery.key ASC, delivery.delivered_at DESC
            """
        ).fetchall()
    scores: dict[str, float] = {}
    for row in rows:
        scores[row["key"]] = scores.get(row["key"], 0.0) + 1.0 / math.sqrt(
            _elapsed_days(row["delivered_at"], current) + 1.0
        )
    return [
        {"key": key, "score": round(score, 9)}
        for key, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    ]


def associations(
    repository: ContextGraphRepository,
    owned_keys: Iterable[str],
    *,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    owned = sorted(set(owned_keys))
    if not owned:
        return []
    with repository.connect() as connection:
        connection.execute(
            "CREATE TEMP TABLE recall_owned_keys(key TEXT PRIMARY KEY) WITHOUT ROWID"
        )
        connection.executemany(
            "INSERT INTO recall_owned_keys(key) VALUES (?)", [(key,) for key in owned]
        )
        rows = connection.execute(
            """
            SELECT candidate.key, COUNT(DISTINCT candidate.session_id) AS sessions
            FROM deliveries seed
            JOIN deliveries candidate ON candidate.session_id = seed.session_id
            WHERE seed.key IN (SELECT key FROM recall_owned_keys)
              AND candidate.key NOT IN (SELECT key FROM recall_owned_keys)
              AND candidate.key != seed.key
              AND (? IS NULL OR candidate.session_id != ?)
              AND EXISTS (
                  SELECT 1 FROM context_nodes topic
                  WHERE topic.namespace = 'memory'
                    AND topic.stable_key = candidate.key
                    AND topic.project IN ('', COALESCE(candidate.project, ''))
              )
            GROUP BY candidate.key
            ORDER BY sessions DESC, candidate.key ASC
            """,
            (session_id, session_id),
        ).fetchall()
    return [{"key": row["key"], "sessions": row["sessions"]} for row in rows]


def lessons(
    repository: ContextGraphRepository,
    *,
    session_id: str | None = None,
    owned_keys: Iterable[str] = (),
    now: int | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    min_corroboration: int = 2,
) -> dict[str, list[dict[str, Any]]]:
    if half_life_days <= 0:
        raise ValueError("half_life_days must be positive")
    current = int(time.time()) if now is None else int(now)
    owned = set(owned_keys)
    query = """
        SELECT delivery.key, delivery.session_id, delivery.delivered_at
        FROM deliveries delivery
        WHERE EXISTS (
            SELECT 1 FROM context_nodes topic
            WHERE topic.namespace = 'memory'
              AND topic.stable_key = delivery.key
              AND topic.project IN ('', COALESCE(delivery.project, ''))
        )
    """
    params: tuple[Any, ...] = ()
    if session_id:
        query += " AND delivery.session_id != ?"
        params = (session_id,)
    query += " ORDER BY delivery.key ASC, delivery.session_id ASC"
    with repository.connect() as connection:
        rows = connection.execute(query, params).fetchall()
    scores: dict[str, float] = {}
    sessions: dict[str, set[str]] = {}
    for row in rows:
        if row["key"] in owned:
            continue
        scores[row["key"]] = scores.get(row["key"], 0.0) + 0.5 ** (
            _elapsed_days(row["delivered_at"], current) / half_life_days
        )
        sessions.setdefault(row["key"], set()).add(row["session_id"])
    ranked = [
        {"key": key, "score": round(score, 9), "sessions": len(sessions[key])}
        for key, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {
        "preferred": [item for item in ranked if item["sessions"] >= min_corroboration],
        "tentative": [item for item in ranked if item["sessions"] < min_corroboration],
    }


def cue(
    repository: ContextGraphRepository,
    directories: Iterable[str | Path],
    *,
    session_id: str,
    project: str | None,
) -> dict[str, Any]:
    normalized = sorted({str(Path(directory).expanduser().resolve()) for directory in directories})
    if not normalized:
        normalized = [str(Path.cwd().resolve())]
    first_visits = [
        directory
        for directory in normalized
        if repository.record_touch(session_id, directory, project=project)
    ]
    with repository.connect() as connection:
        touch_rows = connection.execute(
            "SELECT session_id, project, dir FROM touches ORDER BY session_id, dir"
        ).fetchall()
        delivery_rows = connection.execute(
            "SELECT session_id, key FROM deliveries ORDER BY session_id, key"
        ).fetchall()
    touched_sessions: set[str] = set()
    project_history = False
    for row in touch_rows:
        if project is not None and row["project"] == project and row["session_id"] != session_id:
            project_history = True
        if row["session_id"] == session_id:
            continue
        if any(_paths_related(directory, row["dir"]) for directory in normalized):
            touched_sessions.add(row["session_id"])
    counts: dict[str, set[str]] = {}
    for row in delivery_rows:
        if row["session_id"] in touched_sessions:
            counts.setdefault(row["key"], set()).add(row["session_id"])
    recalled = [
        {"key": key, "sessions": len(session_ids)}
        for key, session_ids in sorted(counts.items(), key=lambda item: (-len(item[1]), item[0]))
    ]
    return {
        "paths": normalized,
        "firstVisits": first_visits,
        "recalled": recalled,
        "unfamiliar": project_history and not touched_sessions,
    }


def _paths_related(left: str, right: str) -> bool:
    left_path = Path(left)
    right_path = Path(right)
    return (
        left_path == right_path
        or left_path in right_path.parents
        or right_path in left_path.parents
    )


def recall_summary(
    repository: ContextGraphRepository,
    *,
    session_id: str | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    owned: list[str] = []
    if session_id:
        with repository.connect() as connection:
            owned = [
                row["key"]
                for row in connection.execute(
                    "SELECT key FROM deliveries WHERE session_id = ? ORDER BY key", (session_id,)
                ).fetchall()
            ]
    lesson_groups = lessons(repository, session_id=session_id, owned_keys=owned, now=now)
    return {
        **lesson_groups,
        "associations": associations(repository, owned, session_id=session_id),
        "activation": activation(repository, now=now),
    }
