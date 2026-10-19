"""The ledger, and the thing it exists to prove: skips are rows."""

from __future__ import annotations

import pytest

import desk_ledger as L
from desk_data import Candidate
from desk_ledger import Decision, Fill, Position, Verdict


def test_schema_creates_every_table(desk_db):
    conn = L.connect(desk_db)
    names = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"candidates", "decisions", "fills", "positions", "equity",
            "gate_verdicts", "runs"} <= names


def test_connect_creates_missing_parent_directory(tmp_path):
    deep = tmp_path / "a" / "b" / "desk.db"
    L.connect(deep)
    assert deep.is_file()


def test_candidate_round_trips_with_its_id(desk_db):
    conn = L.connect(desk_db)
    cid = L.record_candidate(conn, Candidate(
        symbol="SOL", venue="crypto", source="dexscreener", price=1.5,
        liquidity=250_000, vol_24h=90_000, change_24h=12.0,
        params={"fast": 20}, raw={"pairAddress": "abc"}))
    assert cid > 0
    row = conn.execute("SELECT symbol, venue, params_json, raw_json"
                       " FROM candidates WHERE id = ?", (cid,)).fetchone()
    assert row["symbol"] == "SOL"
    assert '"fast": 20' in row["params_json"]
    assert "abc" in row["raw_json"]


def test_a_skip_is_a_first_class_row(desk_db):
    """The whole point: the log can show what it declined, and why."""
    conn = L.connect(desk_db)
    L.record_decision(conn, Decision(symbol="DOGE", action="skip",
                                     vetoed_by="liquidity",
                                     skipped_reason="liquidity 10 below floor"))
    L.record_decision(conn, Decision(symbol="SOL", action="buy", confidence=0.8))

    rows = L.recent_decisions(conn)
    assert len(rows) == 2
    skip = [r for r in rows if r["action"] == "skip"][0]
    assert skip["vetoed_by"] == "liquidity"
    assert "below floor" in skip["skipped_reason"]
    assert L.counts(conn)["skips"] == 1


def test_record_decision_returns_a_usable_id(desk_db):
    conn = L.connect(desk_db)
    did = L.record_decision(conn, Decision(symbol="X", action="buy"))
    assert did > 0
    L.record_fill(conn, Fill(symbol="X", side="buy", qty=1, price=10,
                             decision_id=did))
    assert conn.execute("SELECT decision_id FROM fills").fetchone()[0] == did


def test_verdict_rows_are_written_even_when_they_fail(desk_db):
    conn = L.connect(desk_db)
    L.record_verdict(conn, Verdict(rule_id="ma_cross", symbol="SPY",
                                   verdict="holdout", passed=False,
                                   metrics={"checks": {"n_trades": False}}))
    row = conn.execute("SELECT passed, metrics_json FROM gate_verdicts").fetchone()
    assert row["passed"] == 0
    assert "n_trades" in row["metrics_json"]


def test_positions_upsert_rather_than_duplicate(desk_db):
    conn = L.connect(desk_db)
    L.upsert_position(conn, Position(symbol="SPY", qty=1, avg_price=100))
    L.upsert_position(conn, Position(symbol="SPY", qty=3, avg_price=110))
    held = L.open_positions(conn)
    assert len(held) == 1 and held[0].qty == 3 and held[0].avg_price == 110


def test_delete_position_and_mode_isolation(desk_db):
    conn = L.connect(desk_db)
    L.upsert_position(conn, Position(symbol="SPY", qty=1, avg_price=100, mode="paper"))
    L.upsert_position(conn, Position(symbol="SPY", qty=1, avg_price=100, mode="live"))
    assert len(L.open_positions(conn, "paper")) == 1
    L.delete_position(conn, "SPY", "paper")
    assert L.open_positions(conn, "paper") == []
    assert len(L.open_positions(conn, "live")) == 1


def test_a_position_keeps_the_range_it_was_sized_on(desk_db):
    """The trail and the stop are measured against it after a restart."""
    conn = L.connect(desk_db)
    L.upsert_position(conn, Position(symbol="WILD", qty=1, avg_price=100,
                                     atr_pct=0.11))
    assert L.open_positions(conn)[0].atr_pct == pytest.approx(0.11)


def test_an_old_position_row_without_a_range_reads_as_ordinary(desk_db):
    conn = L.connect(desk_db)
    conn.execute("INSERT INTO positions (ts, symbol, qty, avg_price, mode)"
                 " VALUES ('t', 'OLD', 1, 100, 'paper')")
    assert L.open_positions(conn)[0].atr_pct == pytest.approx(0.04)


def test_connect_widens_a_ledger_written_before_the_range_column(tmp_path):
    """``CREATE TABLE IF NOT EXISTS`` leaves an existing table alone, so the
    edge that keeps a live desk.db usable is the migration, not the DDL."""
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY, ts TEXT,"
                " symbol TEXT, venue TEXT, qty REAL, avg_price REAL, stop REAL,"
                " take REAL, mode TEXT DEFAULT 'paper', UNIQUE(symbol, mode))")
    old.execute("INSERT INTO positions (ts, symbol, qty, avg_price, mode)"
                " VALUES ('t', 'SPY', 1, 100, 'paper')")
    old.commit()
    old.close()

    conn = L.connect(path)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(positions)")}
    assert "atr_pct" in columns
    assert L.open_positions(conn)[0].symbol == "SPY"   # the old row survives


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "desk.db"
    L.connect(path)
    L.connect(path)  # a second open must not try to add the column again


def test_equity_curve_comes_back_oldest_first(desk_db):
    conn = L.connect(desk_db)
    for value in (100.0, 101.0, 102.5):
        L.record_equity(conn, value, cash=value)
    curve = L.equity_curve(conn)
    assert [row["equity"] for row in curve] == [100.0, 101.0, 102.5]


def test_record_run_flags_the_kill_switch(desk_db):
    conn = L.connect(desk_db)
    L.record_run(conn, "paper", 5, 2, 1, True, "daily loss 3.1%")
    row = conn.execute("SELECT kill_switch, notes FROM runs").fetchone()
    assert row["kill_switch"] == 1 and "3.1%" in row["notes"]


def test_now_is_iso8601_utc():
    assert L.now().startswith("20") and "T" in L.now()


# ---------------------------------------------------------------------------
# closed_trades — the desk's own record of itself
# ---------------------------------------------------------------------------
def _fill(symbol, side, qty, price, fees=0.0, venue="crypto", mode="paper"):
    return Fill(symbol=symbol, side=side, qty=qty, price=price, fees=fees,
                venue=venue, mode=mode)


def test_a_round_trip_realises_its_pnl(desk_db):
    conn = L.connect(desk_db)
    L.record_fill(conn, _fill("SOL", "buy", 2.0, 100.0))
    L.record_fill(conn, _fill("SOL", "sell", 2.0, 110.0))
    trades = L.closed_trades(conn, "paper", "crypto")
    assert len(trades) == 1
    assert trades[0]["pnl"] == 20.0 and trades[0]["pnl_pct"] == 10.0


def test_fees_on_both_legs_are_charged(desk_db):
    conn = L.connect(desk_db)
    L.record_fill(conn, _fill("SOL", "buy", 1.0, 100.0, fees=1.0))
    L.record_fill(conn, _fill("SOL", "sell", 1.0, 110.0, fees=1.0))
    assert L.closed_trades(conn, "paper", "crypto")[0]["pnl"] == 8.0


def test_an_open_position_is_not_a_trade(desk_db):
    conn = L.connect(desk_db)
    L.record_fill(conn, _fill("SOL", "buy", 1.0, 100.0))
    assert L.closed_trades(conn, "paper", "crypto") == []


def test_a_partial_sell_closes_only_what_it_sells(desk_db):
    conn = L.connect(desk_db)
    L.record_fill(conn, _fill("SOL", "buy", 4.0, 100.0))
    L.record_fill(conn, _fill("SOL", "sell", 1.0, 120.0))
    trades = L.closed_trades(conn, "paper", "crypto")
    assert len(trades) == 1 and trades[0]["qty"] == 1.0


def test_lots_realise_oldest_first(desk_db):
    """FIFO: a sell must cost out against the earliest buy, not the latest."""
    conn = L.connect(desk_db)
    L.record_fill(conn, _fill("SOL", "buy", 1.0, 100.0))
    L.record_fill(conn, _fill("SOL", "buy", 1.0, 200.0))
    L.record_fill(conn, _fill("SOL", "sell", 1.0, 150.0))
    trade = L.closed_trades(conn, "paper", "crypto")[0]
    assert trade["entry"] == 100.0 and trade["pnl"] == 50.0


def test_mode_and_venue_are_kept_apart(desk_db):
    conn = L.connect(desk_db)
    L.record_fill(conn, _fill("SPY", "buy", 1.0, 100.0, venue="stocks", mode="live"))
    L.record_fill(conn, _fill("SPY", "sell", 1.0, 90.0, venue="stocks", mode="live"))
    L.record_fill(conn, _fill("SPY", "buy", 1.0, 100.0, venue="stocks"))
    assert L.closed_trades(conn, "paper", "stocks") == []
    assert len(L.closed_trades(conn, "live", "stocks")) == 1


def test_a_sell_with_no_buy_is_ignored(desk_db):
    conn = L.connect(desk_db)
    L.record_fill(conn, _fill("SOL", "sell", 1.0, 10.0))
    assert L.closed_trades(conn, "paper", "crypto") == []


def test_the_limit_keeps_the_newest_trades(desk_db):
    conn = L.connect(desk_db)
    for price in (100.0, 110.0, 120.0):
        L.record_fill(conn, _fill("SOL", "buy", 1.0, price))
        L.record_fill(conn, _fill("SOL", "sell", 1.0, price + 5))
    trades = L.closed_trades(conn, "paper", "crypto", limit=1)
    assert len(trades) == 1 and trades[0]["entry"] == 120.0


# ---------------------------------------------------------------------------
# rule attribution — which rule actually made the trade
# ---------------------------------------------------------------------------
def _buy_under(conn, rule_id: str, symbol="SOL", price=100.0):
    """A buy that came from ``rule_id``, through a candidate and a decision."""
    cid = L.record_candidate(conn, Candidate(
        symbol=symbol, venue="crypto", source="dexscreener", rule_id=rule_id,
        price=price))
    did = L.record_decision(conn, Decision(symbol=symbol, action="buy",
                                           candidate_id=cid))
    L.record_fill(conn, Fill(symbol=symbol, side="buy", qty=1.0, price=price,
                             venue="crypto", decision_id=did))
    return cid, did


def test_a_trade_carries_the_rule_that_opened_it(desk_db):
    conn = L.connect(desk_db)
    _buy_under(conn, "breakout")
    L.record_fill(conn, _fill("SOL", "sell", 1.0, 110.0))
    assert L.closed_trades(conn, "paper", "crypto")[0]["rule_id"] == "breakout"


def test_a_fill_with_no_decision_still_closes(desk_db):
    """Older fills predate decision ids; they must not vanish from the book."""
    conn = L.connect(desk_db)
    L.record_fill(conn, _fill("SOL", "buy", 1.0, 100.0))
    L.record_fill(conn, _fill("SOL", "sell", 1.0, 110.0))
    trade = L.closed_trades(conn, "paper", "crypto")[0]
    assert trade["rule_id"] == "" and trade["pnl"] == 10.0


def test_rule_records_group_by_rule_and_drop_unknowns(desk_db):
    conn = L.connect(desk_db)
    _buy_under(conn, "ma_cross", symbol="AAA")
    L.record_fill(conn, _fill("AAA", "sell", 1.0, 90.0))
    _buy_under(conn, "reversion", symbol="BBB")
    L.record_fill(conn, _fill("BBB", "sell", 1.0, 130.0))
    L.record_fill(conn, _fill("CCC", "buy", 1.0, 5.0))
    L.record_fill(conn, _fill("CCC", "sell", 1.0, 6.0))
    L.record_fill(conn, _fill("AAA", "buy", 1.0, 50.0))   # still open, no rule

    grouped = L.rule_records(conn, "paper", "crypto")
    assert set(grouped) == {"ma_cross", "reversion"}
    assert grouped["ma_cross"][0]["pnl"] < 0
    assert grouped["reversion"][0]["pnl"] > 0
