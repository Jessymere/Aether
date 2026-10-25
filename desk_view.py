"""What the Desk tab shows, read straight from the ledger.

Read-only and network-free, so the window can open on the real book before a
cycle has run, and poll it while one is running without slowing the desk.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from desk_ledger import equity_history

#: The judge's model slugs are long; the panel shows a name a person reads.
MODEL_NAMES = {
    "nvidia/nemotron-3-ultra-550b-a55b": "Nemotron 3 Ultra 550B (NIM)",
    "nvidia/nemotron-3-ultra-550b-a55b:free": "Nemotron 3 Ultra 550B",
}

#: Decisions made by code, not by the judge.
_NOT_THE_JUDGE = ("", "gate", "stub", "cache")


def model_name(slug: str) -> str:
    return MODEL_NAMES.get(slug, slug or "—")


def watch_status(action: str, vetoed_by: str, reason: str, rule_id: str) -> str:
    """One line per symbol: where it stands with the desk today."""
    if not vetoed_by and action == "buy":
        return "BOUGHT"
    if vetoed_by == "signal":
        return f"waiting for {rule_id or 'rule'} entry signal"
    if vetoed_by == "judge":
        return f"AI judge passed ({action})"
    if vetoed_by == "held":
        return "holding"
    if vetoed_by == "walk_forward":
        return "benched — " + (reason.replace("every rule benched ", "") or "no rule passed")
    return f"{vetoed_by}: {reason}" if vetoed_by else reason


def overview(conn: sqlite3.Connection, mode: str = "paper", judge_limit: int = 12,
             capital: float | None = None) -> dict[str, Any]:
    """Equity series, watchlist, the judge's recent calls and the latest run.

    ``capital`` is what the book started with (DESK_START_CAPITAL per venue).
    P&L is measured against it, not the first ledger row: the earliest rows
    were a one-venue book, and a second venue's cash read as a 100% gain.
    """
    conn.row_factory = sqlite3.Row
    series = equity_history(conn, mode)[-300:]
    if capital:
        # shortcut: rows far below the book's capital are from a smaller venue
        # set; drop them from the chart. Replace with a venue column if venue
        # sets start changing in normal use.
        series = [(ts, e) for ts, e in series if e >= 0.6 * capital]
    latest = {}
    for r in conn.execute(
            "SELECT d.symbol, d.action, d.vetoed_by, COALESCE(d.skipped_reason, '') AS reason,"
            " d.ts, COALESCE(c.rule_id, '') AS rule_id, c.venue FROM decisions d"
            " LEFT JOIN candidates c ON c.id = d.candidate_id ORDER BY d.id"):
        latest[r["symbol"]] = r  # last row per symbol wins
    watch = sorted(
        ({"symbol": s, "venue": r["venue"] or "", "rule": r["rule_id"], "ts": r["ts"],
          "status": watch_status(r["action"], r["vetoed_by"] or "", r["reason"], r["rule_id"]),
          "validated": bool(r["rule_id"])}
         for s, r in latest.items()),
        key=lambda w: (not w["validated"], w["symbol"]))
    judged = [
        {"ts": r["ts"], "symbol": r["symbol"], "action": r["action"],
         "confidence": r["confidence"] or 0.0, "model": model_name(r["model"] or ""),
         "note": r["reason"]}
        for r in conn.execute(
            "SELECT ts, symbol, action, confidence, model, COALESCE(skipped_reason, '') AS reason"
            f" FROM decisions WHERE brain NOT IN ({','.join('?' * len(_NOT_THE_JUDGE))})"
            " ORDER BY id DESC LIMIT ?", (*_NOT_THE_JUDGE, judge_limit))]
    run = conn.execute("SELECT ts, n_candidates, n_gated, n_filled FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "equity": series[-1][1] if series else None,
        "start_equity": capital or (series[0][1] if series else None),
        "series": [e for _, e in series],
        "watchlist": watch,
        "judge_calls": judged,
        "last_run": dict(run) if run else None,
    }
