"""The three-verdicts gate. Mostly about not fooling ourselves."""

from __future__ import annotations

import pytest

import desk_backtest as B
from desk_backtest import Metrics, Rule, evaluate, pass_criteria, replay
from desk_data import Bar


def bars(n=60, start=100.0, step=1.0):
    return [Bar(ts=f"t{i}", open=start + i * step, high=start + i * step,
                low=start + i * step, close=start + i * step, symbol="T")
            for i in range(n)]


def wavy(n=3650, **kw):
    """The module's own synthetic series, so this file and the gate's offline
    self-test cannot drift apart."""
    return B.synthetic_bars(n=n, **kw)


# ---------------------------------------------------------------------------
# The invariants that matter more than any threshold
# ---------------------------------------------------------------------------
def test_signal_fills_at_the_next_bar_open_not_the_close():
    """The classic look-ahead bug. Here the next bar gaps up 10%."""
    series = [
        Bar(ts="t0", open=100, high=100, low=100, close=100),
        Bar(ts="t1", open=100, high=100, low=100, close=110),
        Bar(ts="t2", open=121, high=121, low=121, close=121),  # +10% gap
        Bar(ts="t3", open=121, high=121, low=121, close=121),
    ]
    enter_on_last_close = Rule("enter", lambda c, p: True, lambda c, p: False)
    metrics = replay(series[:2] + [series[2]] + [series[3]], enter_on_last_close, {},
                     fees_bps=0, slippage_bps=0)
    # Entry at t2's open (121), exit at the final close (121) -> ~0, not +21%.
    assert metrics.n_trades == 1
    assert metrics.expectancy == pytest.approx(0.0, abs=1e-9)


def test_costs_are_charged_on_both_legs():
    flat = bars(6, step=0.0)
    always = Rule("always", lambda c, p: True, lambda c, p: True)
    free = replay(flat, always, {}, fees_bps=0, slippage_bps=0)
    costly = replay(flat, always, {}, fees_bps=100, slippage_bps=0)
    assert free.n_trades == costly.n_trades > 0
    # A flat market is a scratch trade, so free expectancy is exactly zero.
    # Any cost at all must drag it below — on both legs, not just one.
    assert free.expectancy == pytest.approx(0.0, abs=1e-12)
    assert costly.expectancy < free.expectancy
    assert costly.ending_equity < free.ending_equity


def test_a_loss_moves_equity_the_right_way():
    falling = [Bar(ts=f"t{i}", open=100 - i, high=100 - i, low=100 - i,
                   close=100 - i) for i in range(6)]
    always = Rule("always", lambda c, p: True, lambda c, p: True)
    metrics = replay(falling, always, {}, fees_bps=0, slippage_bps=0)
    assert metrics.ending_equity < metrics.starting_equity
    assert metrics.losses > 0


def test_max_drawdown_is_tracked():
    up_then_down = bars(10, step=1.0) + [Bar(ts="x", open=50, high=50, low=50, close=50)]
    always = Rule("always", lambda c, p: True, lambda c, p: False)
    assert replay(up_then_down, always, {}).max_dd > 0


def test_short_input_does_not_crash():
    assert replay([], Rule("r", lambda c, p: True, lambda c, p: True), {}).n_trades == 0
    assert replay(bars(2), Rule("r", lambda c, p: True, lambda c, p: True), {}).n_trades == 0


# ---------------------------------------------------------------------------
# pass_criteria boundaries
# ---------------------------------------------------------------------------
def test_pass_criteria_requires_every_check():
    good = Metrics(n_trades=50, expectancy=0.01, profit_factor=2.0, max_dd=0.05,
                   ending_equity=1.2, starting_equity=1.0)
    assert pass_criteria(good)[0] is True

    for field, bad, check in (
        ("n_trades", 29, "n_trades"),
        ("expectancy", 0.0, "expectancy"),
        ("profit_factor", 1.0, "profit_factor"),
        ("max_dd", 0.26, "max_dd"),
        ("ending_equity", 0.99, "profitable"),
    ):
        broken = Metrics(**{**good.__dict__, field: bad})
        ok, checks = pass_criteria(broken)
        assert ok is False, f"{field}={bad} should fail"
        assert checks[check] is False, f"{field}={bad} must fail the {check} check"


def test_zero_losses_gives_infinite_profit_factor_not_a_crash():
    m = Metrics(n_trades=40, expectancy=0.01, profit_factor=float("inf"),
                max_dd=0.0, ending_equity=1.5)
    assert pass_criteria(m)[0] is True


# ---------------------------------------------------------------------------
# The gate itself
# ---------------------------------------------------------------------------
def test_short_history_is_refused_before_any_split():
    passed, verdicts = evaluate("SHORT", bars(100), B.RULES["ma_cross"])
    assert passed is False
    assert verdicts[0].verdict == "length"


def test_a_flat_market_benches_the_rule_and_says_where():
    flat = [Bar(ts=f"t{i}", open=100, high=100, low=100, close=100)
            for i in range(400)]
    passed, verdicts = evaluate("FLAT", flat, B.RULES["ma_cross"])
    assert passed is False
    assert verdicts and verdicts[0].passed is False
    assert B._first_failure(verdicts).startswith("benched at verdict")


def test_a_running_market_passes_the_gate():
    passed, verdicts = evaluate("TREND", wavy(), B.RULES["ma_cross"])
    assert passed is True, B._first_failure(verdicts)
    assert all(v.passed for v in verdicts)
    # Train, folds, holdout and degradation must all have been considered.
    assert {v.verdict for v in verdicts} >= {"train", "holdout", "degradation"}


def test_a_rule_that_earns_less_than_holding_is_benched_at_benchmark():
    # A gentle wave on a drift clears train, folds, holdout and degradation,
    # but simply holding it earns more, so the desk should just hold.
    passed, verdicts = evaluate("GENTLE", wavy(amp=0.02, period=2.0), B.RULES["ma_cross"])
    assert passed is False
    assert all(v.passed for v in verdicts if v.verdict != "benchmark")
    bench = next(v for v in verdicts if v.verdict == "benchmark")
    assert bench.passed is False
    assert bench.metrics["rule_return"] <= bench.metrics["buy_and_hold"]


def test_buy_and_hold_charges_costs_on_both_legs():
    flat = [Bar(ts=f"t{i}", open=100, high=100, low=100, close=100) for i in range(5)]
    assert B.buy_and_hold(flat, fees_bps=0, slippage_bps=0) == 0
    expected = (1 - 0.003) / (1 + 0.003) - 1  # 10 bps fees + 20 bps slippage each way
    assert abs(B.buy_and_hold(flat, fees_bps=10, slippage_bps=20) - expected) < 1e-12


def test_the_gate_charges_the_costs_it_is_given():
    cheap = evaluate("TREND", wavy(), B.RULES["ma_cross"], costs=(0.0, 0.0))[1]
    dear = evaluate("TREND", wavy(), B.RULES["ma_cross"], costs=(50.0, 100.0))[1]
    train = lambda vs: next(v for v in vs if v.verdict == "train")
    assert train(dear).expectancy < train(cheap).expectancy


def test_verdicts_are_returned_even_on_success():
    passed, verdicts = evaluate("TREND", wavy(), B.RULES["ma_cross"])
    assert verdicts and all(isinstance(v.metrics, dict) for v in verdicts)


def test_proportional_split_is_ordered_and_complete():
    train, validate, holdout = B.proportional_split(bars(100))
    assert len(train) + len(validate) + len(holdout) == 100
    assert len(train) > len(validate) > 0
    assert train[-1].ts != validate[0].ts


def test_self_test_reports_both_directions():
    report = B.self_test()
    assert report["profitable_rule_passes"] is True
    assert report["flat_rule_is_benched"] is True
    assert report["short_history_is_refused"] is True


def test_every_rule_has_a_grid_and_pure_functions():
    for rule in B.RULES.values():
        assert rule.grid and rule.description
        assert rule.entry([100.0] * 300, rule.grid[0]) in (True, False)
        assert rule.exit([100.0] * 300, rule.grid[0]) in (True, False)
