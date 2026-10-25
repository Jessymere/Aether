"""desk_forward and desk_replay, offline on synthetic bars."""

from datetime import date, timedelta

import desk_backtest as B
import desk_forward as F
import desk_replay as R
from desk_data import Bar


def daily(closes, start="2023-06-01", symbol="X"):
    d0 = date.fromisoformat(start)
    return [Bar(ts=(d0 + timedelta(days=i)).isoformat(), open=c, high=c * 1.01,
                low=c * 0.99, close=c, symbol=symbol) for i, c in enumerate(closes)]


ALWAYS = B.Rule(rule_id="always", entry=lambda c, p: True, exit=lambda c, p: False, grid=[{}])


def test_a_forward_rule_cannot_enter_during_the_warm_up():
    bars = daily([100.0 + i for i in range(700)])  # calendar days: reaches 2025
    first = next(i for i, b in enumerate(bars) if b.ts >= F.FORWARD_START)
    m = B.replay(bars, F.forward_rule(ALWAYS, first), {}, fees_bps=0, slippage_bps=0)
    # Entered once, on the first forward bar: the result is the forward-only gain.
    assert m.n_trades == 1
    assert abs(m.ending_equity - bars[-1].close / bars[first + 1].open) < 1e-9


def test_forward_rows_refuse_too_little_warm_up():
    try:
        F.forward_rows("X", [], daily([100.0] * 300, start="2024-11-01"), (0, 0))
    except ValueError as exc:
        assert "warm-up" in str(exc)
    else:
        raise AssertionError("expected a ValueError")


def test_the_summary_compares_passed_with_rejected():
    rows = [{"gate": True, "beat": True, "return": 0.1, "hold": 0.05},
            {"gate": False, "beat": False, "return": 0.0, "hold": 0.2},
            {"gate": False, "beat": True, "return": 0.3, "hold": 0.2}]
    s = F.summarise(rows)
    assert s["passed"] == {"n": 1, "beat": 1, "mean_return": 0.1, "mean_hold": 0.05}
    assert s["rejected"]["n"] == 2 and s["rejected"]["beat"] == 1


def test_replay_buys_at_the_next_open_and_tracks_spy():
    spy = daily([100.0 + i * 0.1 for i in range(700)], symbol="SPY")
    x = daily([50.0 + i * 0.05 for i in range(700)])
    r = R.simulate({"X": (ALWAYS, {})}, {"SPY": spy, "X": x}, capital=10_000, costs=(0, 0))
    assert r["curve"] and r["benchmark"]
    assert r["curve"][0][0] >= F.FORWARD_START
    assert r["hold_return"] > 0                       # SPY only went up
    assert r["return"] > 0 and r["drawdown"] < 0.01   # held X, which only went up
    assert all(t["return"] > 0 for t in r["trades"])  # any exit was the take-profit


def test_replay_with_no_rules_stays_in_cash():
    spy = daily([100.0 + i * 0.1 for i in range(700)], symbol="SPY")
    r = R.simulate({}, {"SPY": spy}, capital=10_000, costs=(0, 0))
    assert r["return"] == 0 and all(e == 10_000 for _, e in r["curve"])
