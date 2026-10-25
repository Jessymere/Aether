"""The runner: the kill switch, and that skips actually reach the ledger."""

from __future__ import annotations

import pytest

import desk_backtest
import desk_runner as R
from desk_data import Bar, Candidate
from desk_ledger import connect, counts, open_positions
from tests.conftest import make_bars

#: ``desk_env`` replaces ``_mark`` with a pass-through, so the tests that are
#: *about* marking re-install the real one.
REAL_MARK = R.DeskRunner._mark


def runner(desk_env, stub_brain, monkeypatch, **kw):
    """A runner pointed entirely at tmp_path, with every network edge stubbed."""
    monkeypatch.setattr(R.DeskRunner, "_dial",
                        lambda self, venue: __import__("desk_seats").Dial(0.8, "test"))
    kw.setdefault("brain", stub_brain)
    venues = kw.pop("venues", ["stocks"])
    return R.DeskRunner(venues=venues, mode="paper", **kw)


def pass_gate(monkeypatch):
    """Stub the walk-forward gate to pass with a rule that signals every day.

    The real gate benches synthetic candles, and since the desk now trades only
    on a validated rule's own signals, a stub must name a rule as well as pass.
    """
    rule = desk_backtest.Rule(rule_id="always", entry=lambda c, p: True,
                              exit=lambda c, p: False, grid=[{}])
    monkeypatch.setitem(R.RULES, "always", rule)

    def walk_forward(self, cand, bars):
        cand.rule_id, cand.params = "always", {}
        return True, "stubbed"

    monkeypatch.setattr(R.DeskRunner, "_walk_forward", walk_forward)


@pytest.fixture
def one_candidate(monkeypatch):
    cand = Candidate(symbol="SPY", venue="stocks", source="yahoo", price=400.0,
                     raw={"bars_count": 400})
    monkeypatch.setattr(R, "scan", lambda venue, limit, source=None: [cand])
    monkeypatch.setattr(R, "bars_for", lambda c, source=None: [])
    pass_gate(monkeypatch)
    return cand


def test_a_cycle_scans_judges_and_fills(desk_env, stub_brain, monkeypatch, one_candidate):
    result = runner(desk_env, stub_brain, monkeypatch).run(cycles=1)
    assert result.scanned == 1 and result.filled == 1
    assert len(result.positions) == 1


def test_the_fill_reaches_the_ledger_with_a_broker_mode(
        desk_env, stub_brain, monkeypatch, one_candidate):
    result = runner(desk_env, stub_brain, monkeypatch).run(cycles=1)
    conn = connect(desk_env["db"])
    row = conn.execute("SELECT symbol, side, mode, broker FROM fills").fetchone()
    assert row["symbol"] == "SPY" and row["side"] == "buy"
    assert row["mode"] == "paper" and row["broker"] == "paper"


def test_a_gate_rejection_is_recorded_as_a_skip_with_its_reason(
        desk_env, stub_brain, monkeypatch):
    """Nothing reaches the judge, and yet the ledger still shows why."""
    thin = Candidate(symbol="THIN", venue="crypto", source="dexscreener",
                     price=1.0, liquidity=10.0, raw={})
    monkeypatch.setattr(R, "scan", lambda venue, limit, source=None: [thin])
    monkeypatch.setattr(R, "bars_for", lambda c, source=None: [])

    result = runner(desk_env, stub_brain, monkeypatch, venues=["crypto"]).run(cycles=1)
    assert result.filled == 0 and result.skips == 1

    conn = connect(desk_env["db"])
    row = conn.execute("SELECT action, vetoed_by, skipped_reason FROM decisions").fetchone()
    assert row["action"] == "skip" and row["vetoed_by"] == "liquidity"
    assert "below floor" in row["skipped_reason"] or "floor" in row["skipped_reason"]


def test_a_judge_skip_is_recorded_too(desk_env, monkeypatch, one_candidate):
    from desk_brain import StubBrain

    result = runner(desk_env, None, monkeypatch,
                    brain=StubBrain("skip", 0.2)).run(cycles=1)
    assert result.filled == 0 and result.skips == 1
    row = connect(desk_env["db"]).execute(
        "SELECT action, skipped_reason FROM decisions").fetchone()
    assert row["action"] == "skip" and "stub" in row["skipped_reason"]


def test_a_low_confidence_buy_is_vetoed_in_code(desk_env, monkeypatch, one_candidate,
                                                monkeypatch_desk=None):
    from desk_brain import StubBrain

    result = runner(desk_env, None, monkeypatch,
                    brain=StubBrain("buy", 0.1)).run(cycles=1)
    assert result.filled == 0 and result.skips == 1
    row = connect(desk_env["db"]).execute("SELECT vetoed_by FROM decisions").fetchone()
    assert row["vetoed_by"] == "confidence"


def test_a_benched_rule_stops_the_candidate(desk_env, stub_brain, monkeypatch):
    """Flat prices bench every rule, so the judge never gets asked."""
    flat = [Bar(ts=f"t{i}", open=100, high=100, low=100, close=100)
            for i in range(400)]
    cand = Candidate(symbol="FLAT", venue="stocks", price=100.0,
                     raw={"bars_count": 400})
    monkeypatch.setattr(R, "scan", lambda venue, limit, source=None: [cand])
    monkeypatch.setattr(R, "bars_for", lambda c, source=None: flat)

    result = runner(desk_env, stub_brain, monkeypatch).run(cycles=1)
    assert result.filled == 0
    assert result.judged == 0  # never reached the judge
    row = connect(desk_env["db"]).execute("SELECT vetoed_by FROM decisions").fetchone()
    assert row["vetoed_by"] == "walk_forward"


def test_verdict_rows_are_written_for_a_benched_rule(desk_env, stub_brain, monkeypatch):
    flat = [Bar(ts=f"t{i}", open=100, high=100, low=100, close=100)
            for i in range(400)]
    cand = Candidate(symbol="FLAT", venue="stocks", price=100.0, raw={"bars_count": 400})
    monkeypatch.setattr(R, "scan", lambda venue, limit, source=None: [cand])
    monkeypatch.setattr(R, "bars_for", lambda c, source=None: flat)
    runner(desk_env, stub_brain, monkeypatch).run(cycles=1)
    assert counts(connect(desk_env["db"]))["gate_verdicts"] > 0


def test_the_gate_is_memoised_within_a_run(desk_env, stub_brain, monkeypatch):
    """A verdict does not change intraday, so the expensive replay runs once.

    Counts the backtest itself rather than ``_walk_forward``. The wrapper is
    correctly called on every cycle; it is the memo *inside* it that has to
    hold. A fresh runner starts with an empty cache, so a three-cycle run must
    cost exactly what a one-cycle run costs, not three times as much.
    """
    import desk_backtest

    monkeypatch.setattr(R, "scan", lambda venue, limit, source=None: [
        Candidate(symbol="SPY", venue="stocks", price=400.0, raw={"bars_count": 400})])
    monkeypatch.setattr(R, "bars_for", lambda c, source=None: make_bars(400))

    calls = {"n": 0}
    real = desk_backtest.evaluate

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(desk_backtest, "evaluate", counting)

    runner(desk_env, stub_brain, monkeypatch).run(cycles=1)
    single = calls["n"]
    assert single > 1  # more than one rule was actually evaluated

    runner(desk_env, stub_brain, monkeypatch).run(cycles=3)
    assert calls["n"] - single == single  # 3 cycles cost the same as 1


# ---------------------------------------------------------------------------
# Volatility — the size and the stop follow the name
# ---------------------------------------------------------------------------
def swinging(n=400, span=1.0):
    """The synthetic uptrend with its candles stretched by ``span``."""
    return [Bar(ts=f"2020-{1 + i % 12:02d}-{1 + i % 28:02d}",
                open=100 + i * 0.4, high=100 + i * 0.4 + span,
                low=100 + i * 0.4 - span, close=100 + i * 0.4)
            for i in range(n)]


def fill_qty(desk_env, stub_brain, monkeypatch, span):
    """One cycle with two otherwise identical names differing only in range.

    The gate is stubbed: this is about sizing, and synthetic candles are
    benched by the real walk-forward gate before the judge is ever reached.
    """
    monkeypatch.setattr(R, "scan", lambda venue, limit, source=None: [
        Candidate(symbol=sym, venue="stocks", price=260.0, raw={"bars_count": 400})
        for sym in ("SPY", "QQQ")])
    monkeypatch.setattr(R, "bars_for",
                        lambda c, source=None: swinging(span=span if c.symbol == "QQQ" else 1.0))
    pass_gate(monkeypatch)
    runner(desk_env, stub_brain, monkeypatch).run(cycles=1)
    conn = connect(desk_env["db"])
    return {r["symbol"]: r["qty"] for r in
            conn.execute("SELECT symbol, qty FROM fills WHERE side = 'buy'")}


def test_a_wilder_name_gets_a_smaller_fill(desk_env, stub_brain, monkeypatch):
    qty = fill_qty(desk_env, stub_brain, monkeypatch, span=8.0)
    assert 0 < qty["QQQ"] < qty["SPY"]


def test_the_filled_position_carries_its_range(desk_env, stub_brain, monkeypatch):
    monkeypatch.setattr(R, "scan", lambda venue, limit, source=None: [
        Candidate(symbol="SPY", venue="stocks", price=260.0, raw={"bars_count": 400})])
    monkeypatch.setattr(R, "bars_for", lambda c, source=None: swinging(span=8.0))
    pass_gate(monkeypatch)

    r = runner(desk_env, stub_brain, monkeypatch)
    r.run(cycles=1)

    held = open_positions(connect(desk_env["db"]))
    assert held[0].atr_pct > 0.04          # the wide name, recorded as wide
    assert held[0].stop < 260.0 * 0.9      # and stopped out further below


# ---------------------------------------------------------------------------
# The kill switch
# ---------------------------------------------------------------------------
def test_kill_switch_trips_and_flattens(desk_env, stub_brain, monkeypatch, one_candidate):
    monkeypatch.setattr(R, "risk_check", lambda **kw: __import__(
        "desk_seats").KillSwitch(True, "daily loss -3.5%"))
    result = runner(desk_env, stub_brain, monkeypatch).run(cycles=1)

    assert result.kill_switch is True
    assert "daily loss" in result.kill_reason
    assert result.positions == []          # flattened
    assert open_positions(connect(desk_env["db"])) == []


def test_a_kill_file_halts_before_any_scan(desk_env, stub_brain, monkeypatch):
    desk_env["kill"].parent.mkdir(parents=True, exist_ok=True)
    desk_env["kill"].write_text("manual\n", encoding="utf-8")
    monkeypatch.setattr(R, "scan", lambda *a, **k: pytest.fail("should not scan"))

    result = runner(desk_env, stub_brain, monkeypatch).run(cycles=1)
    assert result.kill_switch is True and result.scanned == 0


def test_panic_flattens_and_writes_the_kill_file(desk_env, stub_brain, monkeypatch,
                                                 one_candidate):
    r = runner(desk_env, stub_brain, monkeypatch)
    r.run(cycles=1)
    r.panic(reason="test panic")
    assert desk_env["kill"].is_file()
    assert open_positions(connect(desk_env["db"])) == []


def test_a_restart_resumes_the_paper_book_and_its_cash(desk_env, stub_brain,
                                                      monkeypatch, one_candidate):
    """A new runner over the same ledger must not forget the open book.

    Without hydration a restart starts flat at the full starting capital, so
    stops never fire across runs, PANIC sells nothing, and every restart mints
    money. The second runner here is a fresh process in miniature.
    """
    first = runner(desk_env, stub_brain, monkeypatch)
    first.run(cycles=1)
    held = open_positions(connect(desk_env["db"]))

    second = runner(desk_env, stub_brain, monkeypatch)
    book = second._broker("stocks")
    assert [(p.symbol, p.qty) for p in book.positions()] == \
           [(p.symbol, p.qty) for p in held]
    assert book.position("SPY").stop is not None
    assert book.cash() < first.starting_cash      # the fill was paid for, once


def test_state_shows_the_restored_book_before_any_cycle(desk_env, stub_brain,
                                                       monkeypatch, one_candidate):
    first = runner(desk_env, stub_brain, monkeypatch)
    first.run(cycles=1)

    snapshot = runner(desk_env, stub_brain, monkeypatch).state()
    assert [p["symbol"] for p in snapshot["positions"]] == ["SPY"]
    assert snapshot["equity"] is not None and snapshot["equity"] < 10_000


def test_panic_pushes_the_empty_book_into_the_current_state(desk_env, stub_brain,
                                                           monkeypatch, one_candidate):
    r = runner(desk_env, stub_brain, monkeypatch)
    r.run(cycles=1)
    r.panic(reason="state refresh")
    assert r.state()["positions"] == []


def test_panic_on_a_restarted_runner_actually_sells(desk_env, stub_brain,
                                                    monkeypatch, one_candidate):
    first = runner(desk_env, stub_brain, monkeypatch)
    first.run(cycles=1)
    first.resume()

    second = runner(desk_env, stub_brain, monkeypatch)
    second.panic(reason="restart panic")

    conn = connect(desk_env["db"])
    assert open_positions(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM fills WHERE side = 'sell'"
                        ).fetchone()[0] == 1


def test_resume_clears_the_kill_file(desk_env, stub_brain, monkeypatch):
    r = runner(desk_env, stub_brain, monkeypatch)
    r.panic(reason="x")
    r.resume()
    assert not desk_env["kill"].is_file()
    assert r.kill_file_present() is False


# ---------------------------------------------------------------------------
# Resilience
# ---------------------------------------------------------------------------
def test_a_dead_source_does_not_kill_the_run(desk_env, stub_brain, monkeypatch):
    from desk_data import DataUnavailable

    def boom(*a, **k):
        raise DataUnavailable("network down")

    monkeypatch.setattr(R, "scan", boom)
    result = runner(desk_env, stub_brain, monkeypatch).run(cycles=1)
    assert result.scanned == 0 and result.report


def test_a_broken_venue_does_not_stop_the_others(desk_env, stub_brain, monkeypatch):
    monkeypatch.setattr(R, "scan", lambda venue, limit, source=None: [
        Candidate(symbol="SPY", venue="stocks", price=400.0, raw={"bars_count": 400})])
    monkeypatch.setattr(R, "bars_for", lambda c, source=None: [])

    real_cycle = R.DeskRunner._cycle

    def flaky(self, venue, result):
        if venue == "stocks":
            raise ValueError("something odd")
        return real_cycle(self, venue, result)

    monkeypatch.setattr(R.DeskRunner, "_cycle", flaky)
    result = runner(desk_env, stub_brain, monkeypatch,
                    venues=["stocks", "crypto"]).run(cycles=1)
    assert any("something odd" in line for line in result.log)


def test_state_snapshot_is_serialisable(desk_env, stub_brain, monkeypatch, one_candidate):
    r = runner(desk_env, stub_brain, monkeypatch)
    r.run(cycles=1)
    import json

    json.dumps(r.state())  # must not raise: the UI polls this


def test_a_run_row_is_recorded(desk_env, stub_brain, monkeypatch, one_candidate):
    runner(desk_env, stub_brain, monkeypatch).run(cycles=1)
    row = connect(desk_env["db"]).execute(
        "SELECT mode, n_filled FROM runs").fetchone()
    assert row["mode"] == "paper" and row["n_filled"] >= 0


def test_equity_is_recorded_every_cycle(desk_env, stub_brain, monkeypatch, one_candidate):
    runner(desk_env, stub_brain, monkeypatch).run(cycles=2)
    assert counts(connect(desk_env["db"]))["candidates"] >= 1
    assert connect(desk_env["db"]).execute("SELECT COUNT(*) FROM equity").fetchone()[0] == 2


def test_report_is_human_readable(desk_env, stub_brain, monkeypatch, one_candidate):
    result = runner(desk_env, stub_brain, monkeypatch).run(cycles=1)
    assert "Aether desk" in result.report
    assert "scanned" in result.report and "equity" in result.report


def test_an_unfunded_live_venue_says_so(desk_env, stub_brain, monkeypatch):
    """A valid key on an empty account reads fine and refuses every buy.

    Credentials are permission, not balance, so the runner has to surface the
    zero rather than let a live run look healthy while nothing fills.
    """
    live = R.PaperBroker(starting_cash=10.0)
    live.mode = "live"
    live.cash = lambda: 0.0
    monkeypatch.setattr(R, "make_broker", lambda *a, **k: live)

    r = runner(desk_env, stub_brain, monkeypatch)
    r._broker("crypto")

    assert any("no buying power" in line for line in r._log)


def test_a_cash_read_that_raises_does_not_stop_the_venue(desk_env, stub_brain,
                                                        monkeypatch):
    live = R.PaperBroker(starting_cash=10.0)
    live.mode = "live"

    def boom():
        raise RuntimeError("network")

    live.cash = boom
    monkeypatch.setattr(R, "make_broker", lambda *a, **k: live)

    assert runner(desk_env, stub_brain, monkeypatch)._broker("crypto") is live


# ---------------------------------------------------------------------------
# Learning from the record
# ---------------------------------------------------------------------------
def seed_losses(db, n=12, venue="stocks"):
    """n closed losing round trips, written straight to the ledger."""
    from desk_ledger import Fill, record_fill

    conn = connect(db)
    for _ in range(n):
        record_fill(conn, Fill(symbol="SPY", side="buy", qty=1.0, price=100.0,
                               venue=venue, mode="paper"))
        record_fill(conn, Fill(symbol="SPY", side="sell", qty=1.0, price=95.0,
                               venue=venue, mode="paper"))
    return conn


def test_the_record_goes_into_the_judge_facts(desk_env, stub_brain, monkeypatch,
                                              one_candidate):
    seed_losses(desk_env["db"])
    seen = {}

    def capture(cand, facts, brain, candidate_id=None):
        seen.update(facts)
        return brain.decide(facts, symbol=cand.symbol)

    monkeypatch.setattr(R, "judge", capture)
    runner(desk_env, stub_brain, monkeypatch).run(cycles=1)

    assert seen["record_closed_trades"] == 12
    assert seen["record_win_rate"] == 0.0


def test_a_cold_record_cuts_the_dial(desk_env, stub_brain, monkeypatch, one_candidate):
    seed_losses(desk_env["db"])
    r = runner(desk_env, stub_brain, monkeypatch)  # _dial is pinned to 0.8
    result = r.run(cycles=1)
    assert r._records["stocks"]["win_rate"] == 0.0
    # 0.0 win against a 0.4 floor cuts to the 0.25 floor: 0.8 -> 0.2.
    assert any("record 0% win" in line for line in r._log)
    assert result.dial == pytest.approx(0.2)


def test_a_cold_record_is_a_health_warning(desk_env, stub_brain, monkeypatch,
                                           one_candidate):
    seed_losses(desk_env["db"])
    r = runner(desk_env, stub_brain, monkeypatch)
    r.run(cycles=1)
    assert any("size cut" in note for note in r.state()["health"])


def test_the_record_is_in_the_report(desk_env, stub_brain, monkeypatch, one_candidate):
    seed_losses(desk_env["db"])
    result = runner(desk_env, stub_brain, monkeypatch).run(cycles=1)
    assert "closed record:" in result.report and "0% win" in result.report


def test_a_good_record_is_reported_without_a_cut(desk_env, stub_brain, monkeypatch,
                                                 one_candidate):
    from desk_ledger import Fill, record_fill

    conn = connect(desk_env["db"])
    for _ in range(12):
        record_fill(conn, Fill(symbol="SPY", side="buy", qty=1.0, price=100.0,
                               venue="stocks", mode="paper"))
        record_fill(conn, Fill(symbol="SPY", side="sell", qty=1.0, price=110.0,
                               venue="stocks", mode="paper"))

    r = runner(desk_env, stub_brain, monkeypatch)
    result = r.run(cycles=1)
    assert "100% win" in result.report
    assert not any("size cut" in n for n in r.state()["health"])


# ---------------------------------------------------------------------------
# Caching — the desk re-reads bars, not the wire
# ---------------------------------------------------------------------------
def test_bars_are_cached_between_cycles(desk_env, stub_brain, monkeypatch):
    calls = []
    monkeypatch.setattr(R, "bars_for", lambda c, source=None: calls.append(c) or [])

    r = runner(desk_env, stub_brain, monkeypatch)
    cand = Candidate(symbol="SPY", venue="stocks", source="yahoo", price=1.0)
    r._bars(cand)
    r._bars(cand)

    assert len(calls) == 1


def test_the_dial_is_cached_between_cycles(desk_env, stub_brain, monkeypatch):
    """The dial costs two years of benchmark bars; once an hour is plenty."""
    calls = []
    import desk_seats

    monkeypatch.setattr(R, "compute_dial",
                        lambda bars: calls.append(bars) or desk_seats.Dial(0.5, "x"))
    monkeypatch.setattr("desk_data.YahooSource", lambda: type("Y", (), {"bars": lambda *a, **k: []})())

    r = R.DeskRunner(venues=["stocks"], mode="paper", brain=stub_brain)
    r._dial("stocks")
    r._dial("stocks")

    assert len(calls) == 1


def test_the_dial_asks_for_about_a_year_not_a_decade(desk_env, stub_brain, monkeypatch):
    """The 200-day average needs ~290 candles; a decade of them is dead weight."""
    seen = {}

    class Y:
        def bars(self, symbol, start="2015-01-01", end=None, use_cache=True):
            seen["start"] = start
            return []

    monkeypatch.setattr("desk_data.YahooSource", Y)

    r = R.DeskRunner(venues=["stocks"], mode="paper", brain=stub_brain)
    r._compute_dial("stocks")

    from datetime import datetime, timedelta, timezone

    asked = datetime.strptime(seen["start"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - asked
    assert timedelta(days=400) < age < timedelta(days=440)


def test_a_judged_symbol_is_not_re_judged_within_the_ttl(desk_env, stub_brain,
                                                        monkeypatch, one_candidate):
    calls = []

    def counting(cand, facts, brain, candidate_id=None):
        calls.append(cand.symbol)
        return brain.decide(facts, symbol=cand.symbol)

    monkeypatch.setattr(R, "judge", counting)
    runner(desk_env, stub_brain, monkeypatch).run(cycles=3)

    assert calls == ["SPY"]  # three cycles, one opinion


def test_a_cached_verdict_still_writes_its_own_decision_row(desk_env, stub_brain,
                                                           monkeypatch, one_candidate):
    """The cache saves the model call, not the audit trail."""
    import desk_seats

    monkeypatch.setattr(R, "judge", lambda cand, facts, brain, candidate_id=None:
                        desk_seats.Decision(symbol=cand.symbol, action="skip",
                                            confidence=0.9, reason="cached",
                                            candidate_id=candidate_id))

    result = runner(desk_env, stub_brain, monkeypatch).run(cycles=2)
    assert result.judged == 2

    rows = connect(desk_env["db"]).execute(
        "SELECT candidate_id, symbol, action FROM decisions ORDER BY id").fetchall()
    assert len(rows) == 2
    assert [r["action"] for r in rows] == ["skip", "skip"]
    assert rows[0]["candidate_id"] != rows[1]["candidate_id"]  # each its own


def test_a_cached_verdict_does_not_carry_a_stale_veto(desk_env, monkeypatch,
                                                      one_candidate):
    """The first cycle's gate stamp belongs to the first row only."""
    import desk_seats

    monkeypatch.setattr(R, "judge", lambda cand, facts, brain, candidate_id=None:
                        desk_seats.Decision(symbol=cand.symbol, action="buy",
                                            confidence=0.9, candidate_id=candidate_id))

    # The gate vetoes the first cycle and clears the second.
    import desk_seats
    calls = {"n": 0}

    def gate(decision):
        calls["n"] += 1
        if calls["n"] == 1:
            return desk_seats.GateResult(False, "too shy", "confidence")
        return desk_seats.GateResult(True, "cleared", "confidence")

    monkeypatch.setattr(R, "gate_decision", gate)


# ---------------------------------------------------------------------------
# Learning per rule, not just per venue
# ---------------------------------------------------------------------------
def seed_rule(conn, rule_id, n=12, symbol="SPY", venue="stocks", exit_price=95.0):
    """n closed trips opened by ``rule_id``, written straight to the ledger."""
    from desk_data import Candidate
    from desk_ledger import Decision, Fill, record_candidate, record_decision, record_fill

    for _ in range(n):
        cid = record_candidate(conn, Candidate(
            symbol=symbol, venue=venue, source="yahoo", rule_id=rule_id, price=100.0))
        did = record_decision(conn, Decision(symbol=symbol, action="buy",
                                             candidate_id=cid))
        record_fill(conn, Fill(symbol=symbol, side="buy", qty=1.0, price=100.0,
                               venue=venue, mode="paper", decision_id=did))
        record_fill(conn, Fill(symbol=symbol, side="sell", qty=1.0,
                               price=exit_price, venue=venue, mode="paper"))
    return conn


def test_a_rule_is_not_stood_down_on_a_thin_record(desk_env, stub_brain, monkeypatch):
    conn = seed_rule(connect(desk_env["db"]), "ma_cross", n=3)
    r = runner(desk_env, stub_brain, monkeypatch)
    assert r._blocked_rules("stocks") == {}


def test_a_rule_with_enough_losses_is_stood_down(desk_env, stub_brain, monkeypatch):
    seed_rule(connect(desk_env["db"]), "ma_cross")
    r = runner(desk_env, stub_brain, monkeypatch)
    blocked = r._blocked_rules("stocks")
    assert set(blocked) == {"ma_cross"} and blocked["ma_cross"]["win_rate"] == 0.0


def test_a_winning_rule_stays_in_service(desk_env, stub_brain, monkeypatch):
    seed_rule(connect(desk_env["db"]), "ma_cross", exit_price=110.0)
    assert runner(desk_env, stub_brain, monkeypatch)._blocked_rules("stocks") == {}


def test_blocks_are_per_venue(desk_env, stub_brain, monkeypatch):
    seed_rule(connect(desk_env["db"]), "ma_cross", venue="stocks")
    r = runner(desk_env, stub_brain, monkeypatch)
    assert r._blocked_rules("crypto") == {}


def test_a_stood_down_rule_is_never_evaluated(desk_env, stub_brain, monkeypatch):
    """The historical gate is expensive; a benched rule should not cost it."""
    import desk_backtest

    seed_rule(connect(desk_env["db"]), "ma_cross")
    seen = []

    def evaluate(symbol, bars, rule, costs=None):
        seen.append(rule.rule_id)
        return True, []

    monkeypatch.setattr(desk_backtest, "evaluate", evaluate)
    r = runner(desk_env, stub_brain, monkeypatch)
    r._rule_blocks["stocks"] = r._blocked_rules("stocks")

    cand = Candidate(symbol="QQQ", venue="stocks", source="yahoo", price=1.0)
    ok, reason = r._walk_forward(cand, make_bars(5))

    assert ok and "ma_cross" not in seen
    assert cand.rule_id in {"breakout", "reversion"}


def test_when_every_rule_is_stood_down_the_candidate_is_skipped(
        desk_env, stub_brain, monkeypatch):
    import desk_backtest

    seed_rule(connect(desk_env["db"]), "ma_cross")
    seed_rule(connect(desk_env["db"]), "breakout")
    seed_rule(connect(desk_env["db"]), "reversion")
    monkeypatch.setattr(desk_backtest, "evaluate",
                        lambda *a: pytest.fail("no rule should have been run"))

    r = runner(desk_env, stub_brain, monkeypatch)
    r._rule_blocks["stocks"] = r._blocked_rules("stocks")

    ok, reason = r._walk_forward(
        Candidate(symbol="QQQ", venue="stocks", price=1.0), make_bars(5))
    assert not ok and "record-benched" in reason and "ma_cross" in reason


def test_a_cycle_announces_a_stood_down_rule(desk_env, stub_brain, monkeypatch,
                                             one_candidate):
    seed_rule(connect(desk_env["db"]), "ma_cross")
    r = runner(desk_env, stub_brain, monkeypatch)
    r.run(cycles=1)
    assert any("stood down by record" in line for line in r._log)


def test_a_cached_verdict_can_still_pass_the_gate_later(desk_env, stub_brain,
                                                        monkeypatch, one_candidate):
    """The stale veto test's other half: cycle two clears where cycle one did not."""
    import desk_seats

    monkeypatch.setattr(R, "judge", lambda cand, facts, brain, candidate_id=None:
                        desk_seats.Decision(symbol=cand.symbol, action="buy",
                                            confidence=0.9, candidate_id=candidate_id))
    calls = {"n": 0}

    def gate(decision):
        calls["n"] += 1
        if calls["n"] == 1:
            return desk_seats.GateResult(False, "too shy", "confidence")
        return desk_seats.GateResult(True, "cleared", "confidence")

    monkeypatch.setattr(R, "gate_decision", gate)

    r = runner(desk_env, stub_brain, monkeypatch)
    r.run(cycles=2)

    rows = connect(desk_env["db"]).execute(
        "SELECT action, vetoed_by FROM decisions ORDER BY id").fetchall()
    assert rows[0]["vetoed_by"] == "confidence"
    assert rows[1]["vetoed_by"] == ""


def test_the_judge_cache_expires(desk_env, stub_brain, monkeypatch, one_candidate):
    calls = []

    def counting(cand, facts, brain, candidate_id=None):
        calls.append(cand.symbol)
        return brain.decide(facts, symbol=cand.symbol)

    monkeypatch.setattr(R, "judge", counting)
    clock = {"t": 0.0}
    monkeypatch.setattr(R.time, "monotonic", lambda: clock["t"])

    r = runner(desk_env, stub_brain, monkeypatch)
    r._judged(one_candidate, {}, 1)
    clock["t"] = R.JUDGE_CACHE_SECONDS + 1
    r._judged(one_candidate, {}, 2)

    assert len(calls) == 2


def test_a_mark_is_reused_within_a_cycle_and_refreshed_after(desk_env, stub_brain,
                                                             monkeypatch):
    calls = []
    monkeypatch.setattr(R.DeskRunner, "_mark", REAL_MARK)
    monkeypatch.setattr("desk_data.YahooSource.quote",
                        lambda self, symbol: calls.append(symbol) or 100.0)

    clock = {"t": 0.0}
    monkeypatch.setattr(R.time, "monotonic", lambda: clock["t"])

    r = runner(desk_env, stub_brain, monkeypatch)
    assert r._mark("SPY", 1.0) == 100.0
    assert r._mark("SPY", 1.0) == 100.0
    assert len(calls) == 1

    clock["t"] = R.MARK_CACHE_SECONDS + 1
    r._mark("SPY", 1.0)
    assert len(calls) == 2


def test_a_failed_mark_returns_the_fallback_and_is_not_cached(desk_env, stub_brain,
                                                              monkeypatch):
    from desk_data import DataUnavailable

    def boom(self, symbol):
        raise DataUnavailable("down")

    monkeypatch.setattr(R.DeskRunner, "_mark", REAL_MARK)
    monkeypatch.setattr("desk_data.YahooSource.quote", boom)

    r = runner(desk_env, stub_brain, monkeypatch)
    assert r._mark("SPY", 42.0) == 42.0
    assert "SPY" not in r._mark_cache  # a failure is retried next cycle


# ---------------------------------------------------------------------------
# The desk trades the validated rule's own signals
# ---------------------------------------------------------------------------
def test_no_entry_signal_today_means_no_buy(desk_env, stub_brain, monkeypatch, one_candidate):
    pass_gate(monkeypatch)
    monkeypatch.setitem(R.RULES, "always", desk_backtest.Rule(
        rule_id="always", entry=lambda c, p: False, exit=lambda c, p: False, grid=[{}]))
    result = runner(desk_env, stub_brain, monkeypatch).run(cycles=1)
    assert result.filled == 0
    row = connect(desk_env["db"]).execute("SELECT vetoed_by, skipped_reason FROM decisions").fetchone()
    assert row["vetoed_by"] == "signal" and "entry signal" in row["skipped_reason"]


def test_the_rules_exit_signal_sells_and_the_rule_is_recorded(
        desk_env, stub_brain, monkeypatch, one_candidate):
    monkeypatch.setattr(R, "bars_for", lambda c, source=None: make_bars(30))
    exits = {"now": False}
    monkeypatch.setitem(R.RULES, "always", desk_backtest.Rule(
        rule_id="always", entry=lambda c, p: True, exit=lambda c, p: exits["now"], grid=[{}]))
    r = runner(desk_env, stub_brain, monkeypatch)
    r.run(cycles=1)
    conn = connect(desk_env["db"])
    assert open_positions(conn)[0].rule_id == "always"
    assert conn.execute("SELECT rule_id FROM candidates").fetchone()["rule_id"] == "always"

    exits["now"] = True
    monkeypatch.setattr(R, "scan", lambda venue, limit, source=None: [])
    r.run(cycles=1)
    assert open_positions(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM fills WHERE side = 'sell'").fetchone()[0] == 1


def test_missing_history_fails_the_gate_closed(desk_env, stub_brain, monkeypatch):
    r = runner(desk_env, stub_brain, monkeypatch)
    ok, reason = R.DeskRunner._walk_forward(r, Candidate(symbol="SPY", venue="stocks"), [])
    assert ok is False and "no daily history" in reason
