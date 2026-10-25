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


def test_rotation_holds_the_strongest_sectors_and_goes_to_cash_when_all_fall():
    import desk_rotation as ROT
    up = daily([100.0 * 1.001 ** i for i in range(700)], symbol="UP")
    flat = daily([100.0] * 700, symbol="FLAT")
    down = daily([100.0 * 0.999 ** i for i in range(700)], symbol="DOWN")
    r = ROT.rotate({"UP": up, "FLAT": flat, "DOWN": down}, "2024-06-01", top=1, cost_bps=0)
    assert r["return"] > 0 and r["drawdown"] < 1e-9           # always in the rising one
    crash = {"A": daily([100.0 * 0.998 ** i for i in range(700)], symbol="A"),
             "B": daily([100.0 * 0.997 ** i for i in range(700)], symbol="B")}
    r = ROT.rotate(crash, "2024-06-01", top=1, cost_bps=0)
    assert r["return"] == 0                                    # nothing positive: cash


def test_dual_momentum_holds_stocks_in_a_bull_and_bonds_in_a_bear():
    import desk_rotation as ROT
    n = 900
    bull = {"SPY": daily([100 * 1.001 ** i for i in range(n)], symbol="SPY"),
            "BIL": daily([100 * 1.0001 ** i for i in range(n)], symbol="BIL"),
            "AGG": daily([100.0] * n, symbol="AGG")}
    w = ROT.dual_momentum()({s: {b.ts: b.close for b in v} for s, v in bull.items()},
                            [b.ts for b in bull["SPY"]], 800)
    assert w == {"SPY": 1.0}
    bear = dict(bull, SPY=daily([100 * 0.999 ** i for i in range(n)], symbol="SPY"))
    w = ROT.dual_momentum()({s: {b.ts: b.close for b in v} for s, v in bear.items()},
                            [b.ts for b in bear["SPY"]], 800)
    assert w == {"AGG": 1.0}


def test_vol_target_sizes_down_when_spy_is_wild():
    import desk_rotation as ROT
    calm = [100 * (1 + 0.001 * (-1) ** i) for i in range(30)]
    wild = [100 * (1 + 0.05 * (-1) ** i) for i in range(30)]
    cal = [f"d{i:02d}" for i in range(30)]
    assert ROT.vol_target()({"SPY": dict(zip(cal, calm))}, cal, 29)["SPY"] == 1.0
    assert ROT.vol_target()({"SPY": dict(zip(cal, wild))}, cal, 29)["SPY"] < 0.2


def test_chief_budget_takes_a_position_size_override():
    from desk_seats import Dial, chief_budget
    base = chief_budget(10_000, 10_000, Dial(1.0, "t"), 0)
    wide = chief_budget(10_000, 10_000, Dial(1.0, "t"), 0, max_pct=50)
    assert round(wide.per_position) == 5_000 and wide.per_position > base.per_position
