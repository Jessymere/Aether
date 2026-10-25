"""Core + satellite: the core is bought once, never traded, never killed."""

from __future__ import annotations

import desk_runner as R
from desk_ledger import connect, open_positions
from tests.test_desk_runner import pass_gate, runner


def core_runner(desk_env, stub_brain, monkeypatch, pct=80.0):
    monkeypatch.setattr(R, "scan", lambda venue, limit, source=None: [])
    monkeypatch.setattr(R, "bars_for", lambda c, source=None: [])
    monkeypatch.setattr(R.DeskRunner, "_mark",
                        lambda self, symbol, fallback, venue: 100.0)
    pass_gate(monkeypatch)
    r = runner(desk_env, stub_brain, monkeypatch, starting_cash=10_000.0)
    r.core_pct = pct
    return r


def test_the_core_is_bought_once_at_its_share(desk_env, stub_brain, monkeypatch):
    r = core_runner(desk_env, stub_brain, monkeypatch)
    r.run(cycles=2)
    held = {p.symbol: p for p in open_positions(connect(desk_env["db"]), "paper")}
    assert held["SPY"].qty == 80 and held["SPY"].rule_id == "core"
    fills = connect(desk_env["db"]).execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    assert fills == 1  # the second cycle did not buy again


def test_the_kill_switch_keeps_the_core(desk_env, stub_brain, monkeypatch):
    r = core_runner(desk_env, stub_brain, monkeypatch)
    r.run(cycles=1)
    r.resume()
    r.panic(flatten=True, reason="test kill", keep_core=True)
    assert [p.symbol for p in r._broker("stocks").positions()] == ["SPY"]
    r.panic(flatten=True, reason="manual")
    assert r._broker("stocks").positions() == []


def test_the_sleeve_has_its_own_equity_series(desk_env, stub_brain, monkeypatch):
    r = core_runner(desk_env, stub_brain, monkeypatch)
    r.run(cycles=1)
    conn = connect(desk_env["db"])
    rows = dict(conn.execute("SELECT mode, equity FROM equity").fetchall())
    # paper slippage and fees cost a few dollars on the core buy
    assert abs(rows["paper"] - 10_000) < 10 and abs(rows["paper-sleeve"] - 2_000) < 10


def test_reconcile_names_every_disagreement():
    assert R.reconcile({"SPY": 80, "MSFT": 3}, {"SPY": 80, "MSFT": 2, "AAPL": 1}) == [
        "AAPL: ledger 0, broker 1", "MSFT: ledger 3, broker 2"]
    assert R.reconcile({"SPY": 80.0}, {"SPY": 80}) == []


def test_off_by_default_changes_nothing(desk_env, stub_brain, monkeypatch):
    r = core_runner(desk_env, stub_brain, monkeypatch, pct=0.0)
    r.run(cycles=1)
    assert r._broker("stocks").positions() == []
