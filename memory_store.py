"""Small local SQLite store for durable Aether memory."""

import sqlite3
from datetime import datetime
from pathlib import Path


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY, fact TEXT UNIQUE, category TEXT, confidence TEXT, source TEXT, created_at TEXT)")
    connection.execute("CREATE INDEX IF NOT EXISTS facts_text_idx ON facts(fact)")
    return connection


def save_facts(path: Path, facts: list[dict], source: str) -> int:
    stamp = datetime.now().isoformat(timespec="seconds")
    added = 0
    with _connect(path) as connection:
        for item in facts:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO facts(fact, category, confidence, source, created_at) VALUES (?, ?, ?, ?, ?)",
                (item["fact"], item.get("category", "reference"), item.get("confidence", "medium"), source, stamp),
            )
            added += cursor.rowcount
    return added


def recent_context(path: Path, limit: int = 30) -> str:
    if not path.exists():
        return ""
    with _connect(path) as connection:
        rows = connection.execute("SELECT category, confidence, fact FROM facts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    if not rows:
        return ""
    return "--- structured persistent memory ---\n" + "\n".join(
        f"- ({category}, {confidence}) {fact}" for category, confidence, fact in reversed(rows)
    )
