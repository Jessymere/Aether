"""The week on the desk's own record, next to simply holding SPY.

    python -m desk_weekly            # last 7 days
    python -m desk_weekly 30         # last 30 days

Reads only the ledger plus SPY's daily closes. Writes desk/research/weekly.md
and pops one toast. ``main.py --desk`` runs it after the Friday cycle.
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from config import DESK_CORE_SYMBOL, DESK_DB
from desk_forward import RESEARCH_DIR


def build(conn: sqlite3.Connection, since: str, spy: list) -> dict:
    """Pure: the ledger since ``since`` (ISO date) and SPY bars -> the report."""
    conn.row_factory = sqlite3.Row
    mode_row = conn.execute("SELECT mode FROM equity WHERE mode NOT LIKE '%-sleeve'"
                            " ORDER BY id DESC LIMIT 1").fetchone()
    mode = mode_row["mode"] if mode_row else "paper"
    rows = [(r["ts"], r["equity"]) for r in conn.execute(
        "SELECT ts, equity FROM equity WHERE mode = ? ORDER BY id", (mode,))]
    before = [e for ts, e in rows if ts[:10] < since]
    during = [e for ts, e in rows if ts[:10] >= since]
    start = before[-1] if before else (during[0] if during else None)
    end = during[-1] if during else None
    closes = [(b.ts[:10], b.close) for b in spy]
    spy_start = next((c for d, c in reversed(closes) if d < since), None) or \
        next((c for d, c in closes if d >= since), None)
    spy_end = closes[-1][1] if closes else None
    fills = [dict(r) for r in conn.execute(
        "SELECT ts, symbol, side, qty, price, mode FROM fills WHERE ts >= ? ORDER BY id", (since,))]
    actions = Counter(r["action"] for r in conn.execute(
        "SELECT action FROM decisions WHERE ts >= ?", (since,)))
    reasons = Counter(r["skipped_reason"] for r in conn.execute(
        "SELECT skipped_reason FROM decisions WHERE ts >= ? AND action = 'skip'", (since,)))
    return {
        "since": since, "mode": mode,
        "return": end / start - 1 if start and end else None,
        "spy_return": spy_end / spy_start - 1 if spy_start and spy_end else None,
        "start_equity": start, "end_equity": end, "full_window": bool(before),
        "fills": fills, "actions": dict(actions), "reasons": reasons.most_common(5),
    }


def render(r: dict) -> str:
    pct = lambda v: "n/a" if v is None else f"{v:+.2%}"  # noqa: E731
    lines = [f"# Aether desk — since {r['since']} ({r['mode']})", "",
             f"- Desk: {pct(r['return'])}"
             + (f" ({r['start_equity']:,.2f} → {r['end_equity']:,.2f})" if r["end_equity"] else ""),
             f"- Holding SPY: {pct(r['spy_return'])}", ""]
    if not r["full_window"]:
        lines += [f"Too little {r['mode']} history to compare: it starts inside this window.", ""]
    elif r["return"] is not None and r["spy_return"] is not None:
        lines += ["Ahead of SPY." if r["return"] > r["spy_return"] else "Behind SPY.", ""]
    lines.append(f"## Fills ({len(r['fills'])})")
    lines += [f"- {f['ts'][:10]} [{f['mode']}] {f['side']} {f['qty']:g} {f['symbol']} @ {f['price']:g}"
              + (" (core)" if f["symbol"] == DESK_CORE_SYMBOL and f["side"] == "buy" else "")
              for f in r["fills"]] or ["- none"]
    lines += ["", "## Decisions", "- " + (", ".join(f"{k} {v}" for k, v in r["actions"].items()) or "none")]
    if r["reasons"]:
        lines += ["", "## Top skip reasons"] + [f"- {n}× {why}" for why, n in r["reasons"]]
    return "\n".join(lines) + "\n"


def run(days: int = 7) -> dict:
    from desk_alerts import notify
    from desk_data import YahooSource

    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    spy = YahooSource().bars("SPY", start=(datetime.now(timezone.utc)
                                           - timedelta(days=days + 10)).date().isoformat())
    with sqlite3.connect(DESK_DB) as conn:
        report = build(conn, since, spy)
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    (RESEARCH_DIR / "weekly.md").write_text(render(report), encoding="utf-8")
    fmt = lambda v: "n/a" if v is None else f"{v:+.1%}"  # noqa: E731
    notify("Aether desk — weekly report",
           f"Desk {fmt(report['return'])} vs SPY {fmt(report['spy_return'])} · "
           f"{len(report['fills'])} fills")
    return report


if __name__ == "__main__":
    r = run(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
    print(render(r))
