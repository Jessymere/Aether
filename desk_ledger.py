"""Append-only ledger for the trading desk.

One SQLite file at ``config.DESK_DB``. Everything the desk does is written
here — candidates it looked at, decisions it made *including the skips*, the
verdicts that benched a rule, every fill, and the equity curve.

The skip rows are the point. Architecture B's whole argument is that the
paper log has to be able to answer "what did it say no to, and why", so a
``Decision`` is a row whether or not it became an order.

Writes are idempotent by natural key, so re-running a cycle the same day does
not double-count. Reads return plain dataclasses, never raw rows.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import DESK_DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    venue        TEXT NOT NULL,
    source       TEXT NOT NULL,
    rule_id      TEXT,
    params_json  TEXT,
    score        REAL,
    price        REAL,
    liquidity    REAL,
    status       TEXT NOT NULL DEFAULT 'scanned',
    raw_json     TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidates_ts ON candidates(ts);
CREATE INDEX IF NOT EXISTS idx_candidates_symbol ON candidates(symbol);

CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    candidate_id    INTEGER,
    symbol          TEXT NOT NULL,
    action          TEXT NOT NULL,
    confidence      REAL,
    brain           TEXT,
    model           TEXT,
    prompt_hash     TEXT,
    raw_json        TEXT,
    floors_json     TEXT,
    vetoed_by       TEXT,
    skipped_reason  TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts);
CREATE INDEX IF NOT EXISTS idx_decisions_action ON decisions(action);

CREATE TABLE IF NOT EXISTS fills (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    candidate_id INTEGER,
    decision_id  INTEGER,
    symbol       TEXT NOT NULL,
    side         TEXT NOT NULL,
    qty          REAL NOT NULL,
    price        REAL NOT NULL,
    fees         REAL DEFAULT 0,
    venue        TEXT,
    broker       TEXT,
    mode         TEXT DEFAULT 'paper',
    order_id     TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    venue     TEXT,
    qty       REAL NOT NULL,
    avg_price REAL NOT NULL,
    stop      REAL,
    take      REAL,
    atr_pct   REAL,
    mode      TEXT DEFAULT 'paper',
    UNIQUE(symbol, mode)
);

CREATE TABLE IF NOT EXISTS equity (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    mode            TEXT DEFAULT 'paper',
    equity          REAL NOT NULL,
    cash            REAL NOT NULL,
    positions_value REAL DEFAULT 0,
    drawdown        REAL DEFAULT 0,
    day_pnl         REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS gate_verdicts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    candidate_id INTEGER,
    rule_id      TEXT NOT NULL,
    symbol       TEXT,
    verdict      TEXT NOT NULL,
    window       TEXT,
    n_trades     INTEGER,
    expectancy   REAL,
    profit_factor REAL,
    max_dd       REAL,
    passed       INTEGER NOT NULL DEFAULT 0,
    metrics_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_verdicts_rule ON gate_verdicts(rule_id, symbol);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    mode         TEXT,
    n_candidates INTEGER DEFAULT 0,
    n_gated      INTEGER DEFAULT 0,
    n_filled     INTEGER DEFAULT 0,
    kill_switch  INTEGER DEFAULT 0,
    notes        TEXT
);
"""


def now() -> str:
    """ISO-8601 UTC timestamp — the format every table stores."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str = DESK_DB) -> sqlite3.Connection:
    """Open (and create) the desk ledger.

    ``CREATE TABLE IF NOT EXISTS`` never widens a table that already exists, so
    columns added after a ledger has been written are applied here. Every one is
    nullable: an old position row simply has no recorded volatility and falls
    back to the default, rather than failing to load.
    """
    path = Path(path)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    # The desktop creates the runner on the UI thread, runs trading cycles on a
    # worker, and polls read-only snapshots from the UI. SQLite is compiled in
    # serialized mode here (threadsafety == 3), so permit that shared handle.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


#: (table, column, declaration) added after their tables shipped.
_ADDED_COLUMNS = (("positions", "atr_pct", "REAL"),
                  ("positions", "rule_id", "TEXT"),
                  ("positions", "params_json", "TEXT"))


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in _ADDED_COLUMNS:
        have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.commit()


# ---------------------------------------------------------------------------
# Record types
# ---------------------------------------------------------------------------
@dataclass
class Decision:
    """One judging seat's answer. A skip is a Decision too."""

    symbol: str
    action: str  # "buy" | "hold" | "skip"
    confidence: float = 0.0
    reason: str = ""
    candidate_id: Optional[int] = None
    brain: str = ""
    model: str = ""
    raw: dict = field(default_factory=dict)
    vetoed_by: str = ""
    skipped_reason: str = ""
    latency_ms: int = 0
    id: Optional[int] = None


@dataclass
class Fill:
    symbol: str
    side: str  # "buy" | "sell"
    qty: float
    price: float
    fees: float = 0.0
    venue: str = ""
    broker: str = "paper"
    mode: str = "paper"
    order_id: str = ""
    candidate_id: Optional[int] = None
    decision_id: Optional[int] = None


@dataclass
class Position:
    symbol: str
    qty: float
    avg_price: float
    venue: str = ""
    stop: Optional[float] = None
    take: Optional[float] = None
    #: Daily range at entry, as a fraction (0.04 == 4%). Keeps the stop and the
    #: trail a constant fraction of *this* name's noise instead of one default
    #: stretched over both a meme coin and the S&P.
    atr_pct: float = 0.04
    mode: str = "paper"
    #: The gate-validated rule that opened it, and its tuned parameters, so the
    #: position exits on the same signal the backtest exited on.
    rule_id: str = ""
    params: dict = field(default_factory=dict)


@dataclass
class Verdict:
    """One walk-forward verdict. ``passed`` False benches the rule."""

    rule_id: str
    verdict: str  # "train" | "folds" | "holdout" | "degradation" | "benchmark"
    symbol: str = ""
    window: str = ""
    n_trades: int = 0
    expectancy: float = 0.0
    profit_factor: float = 0.0
    max_dd: float = 0.0
    passed: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)
    candidate_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def record_candidate(conn: sqlite3.Connection, cand: Any) -> int:
    """Insert a candidate and return its id."""
    cur = conn.execute(
        "INSERT INTO candidates (ts, symbol, venue, source, rule_id, params_json,"
        " score, price, liquidity, status, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            now(),
            cand.symbol,
            getattr(cand, "venue", ""),
            getattr(cand, "source", ""),
            getattr(cand, "rule_id", ""),
            json.dumps(getattr(cand, "params", {}) or {}, sort_keys=True),
            float(getattr(cand, "score", 0.0) or 0.0),
            float(getattr(cand, "price", 0.0) or 0.0),
            float(getattr(cand, "liquidity", 0.0) or 0.0),
            getattr(cand, "status", "scanned"),
            json.dumps(getattr(cand, "raw", {}) or {}, default=str, sort_keys=True),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def set_candidate_status(conn: sqlite3.Connection, candidate_id: int, status: str) -> None:
    conn.execute("UPDATE candidates SET status = ? WHERE id = ?", (status, candidate_id))
    conn.commit()


def record_decision(conn: sqlite3.Connection, dec: Decision) -> int:
    cur = conn.execute(
        "INSERT INTO decisions (ts, candidate_id, symbol, action, confidence, brain,"
        " model, prompt_hash, raw_json, vetoed_by, skipped_reason)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            now(),
            dec.candidate_id,
            dec.symbol,
            dec.action,
            float(dec.confidence or 0.0),
            dec.brain,
            dec.model,
            dec.raw.get("prompt_hash", "") if dec.raw else "",
            json.dumps(dec.raw or {}, default=str, sort_keys=True),
            dec.vetoed_by,
            dec.skipped_reason or dec.reason,
        ),
    )
    conn.commit()
    dec.id = int(cur.lastrowid)
    return dec.id


def record_fill(conn: sqlite3.Connection, fill: Fill) -> int:
    cur = conn.execute(
        "INSERT INTO fills (ts, candidate_id, decision_id, symbol, side, qty, price,"
        " fees, venue, broker, mode, order_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            now(),
            fill.candidate_id,
            fill.decision_id,
            fill.symbol,
            fill.side,
            float(fill.qty),
            float(fill.price),
            float(fill.fees),
            fill.venue,
            fill.broker,
            fill.mode,
            fill.order_id,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def record_verdict(conn: sqlite3.Connection, v: Verdict) -> int:
    cur = conn.execute(
        "INSERT INTO gate_verdicts (ts, candidate_id, rule_id, symbol, verdict, window,"
        " n_trades, expectancy, profit_factor, max_dd, passed, metrics_json)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            now(),
            v.candidate_id,
            v.rule_id,
            v.symbol,
            v.verdict,
            v.window,
            int(v.n_trades),
            float(v.expectancy),
            float(v.profit_factor),
            float(v.max_dd),
            1 if v.passed else 0,
            json.dumps(v.metrics, default=str, sort_keys=True),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def record_equity(conn: sqlite3.Connection, equity: float, cash: float,
                  positions_value: float = 0.0, drawdown: float = 0.0,
                  day_pnl: float = 0.0, mode: str = "paper") -> int:
    cur = conn.execute(
        "INSERT INTO equity (ts, mode, equity, cash, positions_value, drawdown, day_pnl)"
        " VALUES (?,?,?,?,?,?,?)",
        (now(), mode, float(equity), float(cash), float(positions_value),
         float(drawdown), float(day_pnl)),
    )
    conn.commit()
    return int(cur.lastrowid)


def equity_history(conn: sqlite3.Connection, mode: str = "paper") -> list[tuple[str, float]]:
    """``(ts, equity)`` for one mode, oldest first — what the kill switch reads."""
    return [(r["ts"], r["equity"]) for r in conn.execute(
        "SELECT ts, equity FROM equity WHERE mode = ? ORDER BY id", (mode,))]


def record_run(conn: sqlite3.Connection, mode: str, n_candidates: int, n_gated: int,
               n_filled: int, kill_switch: bool, notes: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO runs (ts, mode, n_candidates, n_gated, n_filled, kill_switch, notes)"
        " VALUES (?,?,?,?,?,?,?)",
        (now(), mode, n_candidates, n_gated, n_filled, 1 if kill_switch else 0, notes),
    )
    conn.commit()
    return int(cur.lastrowid)


def upsert_position(conn: sqlite3.Connection, pos: Position) -> None:
    conn.execute(
        "INSERT INTO positions (ts, symbol, venue, qty, avg_price, stop, take,"
        " atr_pct, mode, rule_id, params_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(symbol, mode) DO UPDATE SET ts=excluded.ts, qty=excluded.qty,"
        " avg_price=excluded.avg_price, stop=excluded.stop, take=excluded.take,"
        " atr_pct=excluded.atr_pct, rule_id=excluded.rule_id,"
        " params_json=excluded.params_json",
        (now(), pos.symbol, pos.venue, float(pos.qty), float(pos.avg_price),
         pos.stop, pos.take, float(pos.atr_pct or 0.04), pos.mode,
         pos.rule_id, json.dumps(pos.params or {})),
    )
    conn.commit()


def delete_position(conn: sqlite3.Connection, symbol: str, mode: str = "paper") -> None:
    conn.execute("DELETE FROM positions WHERE symbol = ? AND mode = ?", (symbol, mode))
    conn.commit()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def open_positions(conn: sqlite3.Connection, mode: str = "paper") -> list[Position]:
    rows = conn.execute(
        "SELECT symbol, venue, qty, avg_price, stop, take, atr_pct, mode,"
        " rule_id, params_json FROM positions WHERE mode = ? AND qty > 0",
        (mode,),
    ).fetchall()
    return [
        Position(symbol=r["symbol"], qty=r["qty"], avg_price=r["avg_price"],
                 venue=r["venue"] or "", stop=r["stop"], take=r["take"],
                 atr_pct=r["atr_pct"] if r["atr_pct"] is not None else 0.04,
                 mode=r["mode"], rule_id=r["rule_id"] or "",
                 params=json.loads(r["params_json"] or "{}"))
        for r in rows
    ]


def set_candidate_rule(conn: sqlite3.Connection, candidate_id: int,
                       rule_id: str, params: dict) -> None:
    """Stamp the rule that cleared the gate. The row is written before the gate
    runs, so without this every candidate's rule_id stayed '' and the per-rule
    record could never stand a losing rule down."""
    conn.execute("UPDATE candidates SET rule_id = ?, params_json = ? WHERE id = ?",
                 (rule_id, json.dumps(params or {}), candidate_id))
    conn.commit()


def fills_for(conn: sqlite3.Connection, mode: str = "paper",
              venue: Optional[str] = None) -> list[Fill]:
    """Every recorded fill for a mode (and, when given, one venue), oldest first.

    Used to rebuild a paper account after a restart: cash is a running total of
    these, so the desk must never re-derive it from the starting capital alone
    or a restart would mint money.
    """
    sql = ("SELECT symbol, side, qty, price, fees, venue, broker, mode"
           " FROM fills WHERE mode = ?")
    args: list = [mode]
    if venue is not None:
        sql += " AND venue = ?"
        args.append(venue)
    rows = conn.execute(sql + " ORDER BY id", tuple(args)).fetchall()
    return [
        Fill(symbol=r["symbol"], side=r["side"], qty=r["qty"], price=r["price"],
             fees=r["fees"] or 0.0, venue=r["venue"] or "", broker=r["broker"] or "",
             mode=r["mode"])
        for r in rows
    ]


def closed_trades(conn: sqlite3.Connection, mode: str = "paper",
                  venue: Optional[str] = None, limit: int = 30) -> list[dict]:
    """Completed round trips, newest last, from the fills alone.

    FIFO against a per-symbol running cost basis: a sell realises the oldest
    units still held. Fees on both legs are charged to the trade. This is the
    desk's own record of itself — the thing it can learn from without any
    model being retrained.

    A partial sell closes only what it sells, so a scale-out and its remainder
    stay separate trades. Each trade carries the ``rule_id`` that opened it
    (through the decision and its candidate) so the record can be read per
    rule as well as per venue.
    """
    sql = ("SELECT f.symbol, f.side, f.qty, f.price, f.fees, f.venue, c.rule_id"
           " FROM fills f"
           " LEFT JOIN decisions d ON d.id = f.decision_id"
           " LEFT JOIN candidates c ON c.id = d.candidate_id"
           " WHERE f.mode = ?")
    args: list = [mode]
    if venue is not None:
        sql += " AND f.venue = ?"
        args.append(venue)
    rows = conn.execute(sql + " ORDER BY f.id", tuple(args)).fetchall()

    #: symbol -> [[qty, price, fee_per_unit, rule_id], ...], oldest first
    book: dict[str, list[list[float]]] = {}
    trades: list[dict] = []
    for row in rows:
        symbol, side = row["symbol"], row["side"]
        qty, price = float(row["qty"] or 0.0), float(row["price"] or 0.0)
        fees = float(row["fees"] or 0.0)
        held = book.setdefault(symbol, [])
        if side == "buy":
            if qty > 0:
                held.append([qty, price, fees / qty if qty else 0.0,
                             row["rule_id"] or ""])
            continue
        remaining = qty
        while remaining > 1e-12 and held:
            lot = held[0]
            take = min(remaining, lot[0])
            entry, entry_fee = lot[1], lot[2]
            exit_fee = fees / qty if qty else 0.0
            gross = (price - entry) * take
            cost = (entry_fee + exit_fee) * take
            pct = ((price - entry) / entry * 100) if entry else 0.0
            trades.append({"symbol": symbol, "venue": row["venue"] or "",
                           "entry": entry, "exit": price, "qty": take,
                           "rule_id": lot[3],
                           "pnl": round(gross - cost, 6),
                           "pnl_pct": round(pct, 4)})
            lot[0] -= take
            remaining -= take
            if lot[0] <= 1e-12:
                held.pop(0)
    return trades[-limit:]


def rule_records(conn: sqlite3.Connection, mode: str = "paper",
                 venue: Optional[str] = None, limit: int = 200) -> dict[str, list[dict]]:
    """Closed trades grouped by the rule that opened them.

    The venue record says *whether* the desk is working; this says *which of
    its rules* is not. A rule whose own trades lose is benched by name, which
    is narrower than cutting size on everything.
    """
    grouped: dict[str, list[dict]] = {}
    for trade in closed_trades(conn, mode, venue, limit=limit):
        if trade["rule_id"]:
            grouped.setdefault(trade["rule_id"], []).append(trade)
    return grouped


def mint_map(conn: sqlite3.Connection) -> dict[str, str]:
    """``symbol -> mint`` from recorded candidates, newest row winning.

    The runner writes the raw candidate JSON down; this reads the one field
    back out. Kept in the ledger rather than memory because a live sell after
    a restart has no candidate to ask.
    """
    out: dict[str, str] = {}
    rows = conn.execute(
        "SELECT symbol, raw_json FROM candidates WHERE raw_json LIKE '%baseAddress%'"
        " ORDER BY id")
    for row in rows:
        try:
            mint = str((json.loads(row["raw_json"]) or {}).get("baseAddress") or "")
        except (ValueError, TypeError, AttributeError):
            continue
        if mint:
            out[row["symbol"]] = mint
    return out


def recent_decisions(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT ts, symbol, action, confidence, skipped_reason, vetoed_by"
        " FROM decisions ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()


def equity_curve(conn: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT ts, equity, cash, positions_value, drawdown, day_pnl FROM equity"
        " ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return list(reversed(rows))


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    out = {}
    for table in ("candidates", "decisions", "fills", "gate_verdicts", "runs"):
        out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    out["skips"] = conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE action = 'skip'"
    ).fetchone()[0]
    return out
