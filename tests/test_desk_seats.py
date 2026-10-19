"""The seats, the wall between them, and every hard floor."""

from __future__ import annotations

import pytest

import desk_seats as S
from desk_data import Bar, Candidate


# ---------------------------------------------------------------------------
# The wall
# ---------------------------------------------------------------------------
def test_no_seat_both_trades_and_opines():
    for seat in S.SEATS.values():
        assert not (seat.places_orders and seat.has_opinion), seat.name


def test_exactly_one_seat_has_an_opinion_and_it_does_not_trade():
    opinionated = [s for s in S.SEATS.values() if s.has_opinion]
    assert [s.name for s in opinionated] == ["judge"]
    assert opinionated[0].places_orders is False


def test_exactly_one_seat_places_orders():
    assert [s.name for s in S.SEATS.values() if s.places_orders] == ["fills"]


def test_separation_is_enforced_at_call_time(monkeypatch):
    broken = dict(S.SEATS)
    broken["bad"] = S.Seat("bad", "model", True, True, "trades and opines")
    monkeypatch.setattr(S, "SEATS", broken)
    with pytest.raises(AssertionError):
        S.assert_separation()


# ---------------------------------------------------------------------------
# The dial
# ---------------------------------------------------------------------------
def rising(n=260):
    return [Bar(ts=f"t{i}", open=0, high=0, low=0, close=100.0 + i) for i in range(n)]


def falling(n=260):
    return [Bar(ts=f"t{i}", open=0, high=0, low=0, close=300.0 - i) for i in range(n)]


def test_dial_is_always_within_bounds():
    for series in (rising(), falling(), [], rising(30)):
        value = S.compute_dial(series).value
        assert 0.0 <= value <= 1.0


def test_dial_rises_with_trend_and_falls_with_it():
    assert S.compute_dial(rising()).value > S.compute_dial(falling()).value


def test_thin_history_gets_a_cautious_dial():
    assert S.compute_dial(rising(10)).value <= 0.3
    assert "bars" in S.compute_dial(rising(10)).reason


def test_dial_is_deterministic():
    assert S.compute_dial(rising()).value == S.compute_dial(rising()).value


# ---------------------------------------------------------------------------
# Chief / SIZE — pure arithmetic, ceilings applied in code
# ---------------------------------------------------------------------------
def test_chief_applies_the_position_ceiling(monkeypatch):
    monkeypatch.setattr(S, "DESK_MAX_POSITION_PCT", 10.0)
    monkeypatch.setattr(S, "DESK_MAX_POSITIONS", 8)
    budget = S.chief_budget(10_000, 10_000, S.Dial(1.0, "full"), open_count=0)
    assert budget.per_position == pytest.approx(1_000)  # 10% of equity


def test_chief_is_capped_by_available_cash():
    budget = S.chief_budget(10_000, 50.0, S.Dial(1.0, "full"), open_count=0)
    assert budget.per_position == pytest.approx(50.0)


def test_dial_scales_the_budget_and_is_the_first_multiplier():
    full = S.chief_budget(10_000, 10_000, S.Dial(1.0, "full"), 0)
    half = S.chief_budget(10_000, 10_000, S.Dial(0.5, "half"), 0)
    assert half.per_position == pytest.approx(full.per_position / 2)


def test_slots_shrink_as_positions_open(monkeypatch):
    monkeypatch.setattr(S, "DESK_MAX_POSITIONS", 4)
    assert S.chief_budget(1000, 1000, S.Dial(1.0, ""), open_count=3).slots == 1


def test_size_position_scales_with_confidence():
    assert S.size_position(1000, 10, 1.0) == pytest.approx(100)
    assert S.size_position(1000, 10, 0.5) == pytest.approx(50)


def test_size_position_guards_zero_inputs():
    assert S.size_position(0, 10, 1.0) == 0
    assert S.size_position(1000, 0, 1.0) == 0


def test_stop_is_below_and_take_is_above():
    stop, take = S.stop_and_take(100.0)
    assert stop < 100.0 < take


# ---------------------------------------------------------------------------
# Volatility — the same bad day must cost the same dollars everywhere
# ---------------------------------------------------------------------------
def bars_of_range(pct, n=30, price=100.0):
    """Closes flat, each candle spanning ``pct`` of price around it."""
    half = price * pct / 2
    return [Bar(ts=f"t{i}", open=price, high=price + half, low=price - half,
                close=price) for i in range(n)]


def test_atr_reads_the_range_of_the_bars():
    assert S.atr_pct(bars_of_range(0.02)) == pytest.approx(0.02, rel=0.05)


def test_atr_counts_a_gap_not_just_the_candle():
    """A gap is noise too: a 10% air pocket with no intraday range still hurts."""
    bars = [Bar(ts="a", open=100, high=100, low=100, close=100),
            Bar(ts="b", open=110, high=110, low=110, close=110)]
    assert S.atr_pct(bars) == pytest.approx(10 / 110)


def test_atr_falls_back_without_history():
    assert S.atr_pct([]) == pytest.approx(S.DEFAULT_ATR_PCT)
    assert S.atr_pct([Bar(ts="a", open=1, high=1, low=1, close=1)]) == \
        pytest.approx(S.DEFAULT_ATR_PCT)


def test_atr_is_clamped_at_both_ends():
    assert S.atr_pct(bars_of_range(0.0001)) == pytest.approx(0.005)
    assert S.atr_pct(bars_of_range(0.90)) == pytest.approx(0.25)


def test_a_volatile_name_is_sized_down_and_a_calm_one_is_not_lifted():
    calm = S.size_position(1000, 10, 1.0, S.DEFAULT_ATR_PCT / 2)
    normal = S.size_position(1000, 10, 1.0, S.DEFAULT_ATR_PCT)
    wild = S.size_position(1000, 10, 1.0, S.DEFAULT_ATR_PCT * 2)

    assert calm == normal == pytest.approx(100)   # never above the budget
    assert wild == pytest.approx(50)              # half the cash, same risk


def test_a_wild_name_never_drops_below_a_quarter_size():
    assert S.size_position(1000, 10, 1.0, 1.0) == pytest.approx(25)


def test_the_cash_at_risk_is_roughly_flat_across_noisy_names():
    """Size x stop distance is the dollars a stop-out costs. Past the reference
    range the two move in step, so a wilder name is not a bigger bet."""
    def risk(atr):
        price = 100.0
        qty = S.size_position(10_000, price, 1.0, atr)
        stop, _ = S.stop_and_take(price, atr)
        return qty * (price - stop)

    reference = risk(S.DEFAULT_ATR_PCT)
    assert risk(S.DEFAULT_ATR_PCT * 2) == pytest.approx(reference, rel=0.02)
    assert risk(S.DEFAULT_ATR_PCT * 3) == pytest.approx(reference, rel=0.02)


def test_the_stop_widens_with_the_name():
    tight, _ = S.stop_and_take(100.0, 0.01)
    loose, _ = S.stop_and_take(100.0, 0.10)
    assert loose < tight < 100.0


def test_a_position_carries_the_range_it_was_sized_on():
    position = S.position_from_fill("X", 1, 100.0, "crypto", "paper", 0.09)
    assert position.atr_pct == pytest.approx(0.09)
    assert position.stop == pytest.approx(100.0 * 0.82)


def test_the_judge_sees_the_range_it_is_betting_into():
    facts = S.judge_evidence(crypto(), bars_of_range(0.06), S.Dial(0.5, "x"))
    assert facts["daily_range_pct"] == pytest.approx(6.0, rel=0.05)
    assert facts["stop_distance_pct"] == pytest.approx(12.0, rel=0.05)


def test_should_exit_fires_on_stop_and_target():
    position = S.position_from_fill("X", 1, 100.0, "stocks", "paper")
    assert S.should_exit(position, position.stop) is not None
    assert S.should_exit(position, position.take) is not None
    assert S.should_exit(position, 100.0) is None


# ---------------------------------------------------------------------------
# Gates — every floor, and the first failure is named
# ---------------------------------------------------------------------------
def crypto(**kw):
    base = dict(symbol="MEME", venue="crypto", price=1.0, liquidity=500_000.0)
    base.update(kw)
    return Candidate(**base)


def test_liquidity_floor_rejects_thin_pools(monkeypatch):
    monkeypatch.setattr(S, "DESK_LIQUIDITY_FLOOR", 50_000.0)
    result = S.gate_candidate(crypto(liquidity=49_999.0))
    assert result.ok is False and result.check == "liquidity"


def test_liquidity_floor_does_not_apply_to_stocks(monkeypatch):
    monkeypatch.setattr(S, "DESK_LIQUIDITY_FLOOR", 50_000.0)
    assert S.gate_candidate(Candidate(symbol="SPY", venue="stocks", price=400.0,
                                      liquidity=0.0)).ok is True


def test_gate_rejects_no_price_held_slotless_and_cooldown():
    assert S.gate_candidate(crypto(price=0)).check == "price"
    assert S.gate_candidate(crypto(), already_held=True).check == "held"
    assert S.gate_candidate(crypto(), slots=0).check == "slots"
    assert S.gate_candidate(crypto(), cooldown=True).check == "cooldown"


def test_history_gate_only_bites_when_bars_are_known(monkeypatch):
    monkeypatch.setattr(S, "DESK_MIN_BARS", 250)
    assert S.gate_candidate(crypto(), bars_count=100).check == "history"
    assert S.gate_candidate(crypto(), bars_count=0).ok is True


def test_confidence_floor_is_enforced_in_code_not_by_the_model(monkeypatch):
    from desk_ledger import Decision

    monkeypatch.setattr(S, "DESK_CONFIDENCE_FLOOR", 0.6)
    assert S.gate_decision(Decision("X", "buy", 0.59)).check == "confidence"
    assert S.gate_decision(Decision("X", "buy", 0.60)).ok is True
    assert S.gate_decision(Decision("X", "skip", 0.99)).check == "judge"


# ---------------------------------------------------------------------------
# RISK — the kill switch
# ---------------------------------------------------------------------------
def test_kill_switch_trips_on_daily_loss(monkeypatch):
    monkeypatch.setattr(S, "DESK_DAILY_LOSS_LIMIT_PCT", 3.0)
    assert S.risk_check(day_pnl_pct=-3.0, drawdown_pct=0, losing_days=0).tripped


def test_kill_switch_trips_on_drawdown(monkeypatch):
    monkeypatch.setattr(S, "DESK_MAX_DRAWDOWN_PCT", 15.0)
    assert S.risk_check(day_pnl_pct=0, drawdown_pct=15.0, losing_days=0).tripped


def test_kill_switch_trips_on_losing_streak(monkeypatch):
    monkeypatch.setattr(S, "DESK_MAX_LOSING_DAYS", 3)
    assert S.risk_check(day_pnl_pct=0, drawdown_pct=0, losing_days=3).tripped


def test_kill_switch_stays_quiet_when_within_limits():
    assert S.risk_check(day_pnl_pct=-1.0, drawdown_pct=2.0, losing_days=0).tripped is False


# ---------------------------------------------------------------------------
# JUDGE evidence — no arithmetic left for the model
# ---------------------------------------------------------------------------
def test_judge_evidence_is_all_finished_numbers(synth_bars):
    facts = S.judge_evidence(crypto(price=2.0), synth_bars, S.Dial(0.7, "x"))
    assert isinstance(facts["daily_dial_0_to_1"], float)
    assert isinstance(facts["pct_vs_20day_mean"], float)
    assert isinstance(facts["bars_of_history"], int)
    assert "liquidity_usd" in facts and "venue" in facts


def test_judge_evidence_survives_no_bars():
    facts = S.judge_evidence(crypto(), [], S.Dial(0.0, "x"))
    assert facts["bars_of_history"] == 0
    assert facts["pct_vs_20day_mean"] == 0.0


def test_score_candidate_ranks_turnover_over_size():
    busy = crypto(symbol="A", vol_24h=1_000_000, change_24h=0)
    quiet = crypto(symbol="B", vol_24h=100, change_24h=0)
    assert S.score_candidate(busy) > S.score_candidate(quiet)


def test_self_test_passes():
    report = S.self_test()
    assert report["one_opinion"] is True
    assert report["dial_reacts"] is True


# ---------------------------------------------------------------------------
# The trailing stop — a winner must not be given back whole
# ---------------------------------------------------------------------------
def _pos(entry=100.0, stop=90.0):
    from desk_ledger import Position

    return Position(symbol="A", qty=1.0, avg_price=entry, stop=stop, mode="paper")


def test_the_stop_never_moves_down():
    pos = _pos(entry=100.0, stop=95.0)
    assert S.trail_stop(pos, 101.0) >= 95.0


def test_a_flat_position_keeps_a_tighter_stop():
    """At entry the trail rung equals the default stop, so a stop already
    tighter than that must survive a price that has barely moved."""
    assert S.trail_stop(_pos(stop=95.0), 100.5) == 95.0


def test_a_full_stop_distance_up_moves_the_stop_to_breakeven():
    assert S.trail_stop(_pos(), 108.5) == pytest.approx(100.0)


def test_a_runner_trails_a_stop_distance_below_the_mark():
    assert S.trail_stop(_pos(), 120.0) == pytest.approx(120.0 * 0.92)


def test_a_zero_price_keeps_the_stop():
    assert S.trail_stop(_pos(), 0.0) == 90.0


# ---------------------------------------------------------------------------
# The record — the desk's own results, and how they feed back
# ---------------------------------------------------------------------------
def _trade(pnl, pct):
    return {"pnl": pnl, "pnl_pct": pct}


def test_an_empty_record_has_no_rate():
    summary = S.record_summary([])
    assert summary["closed_trades"] == 0 and summary["win_rate"] is None


def test_the_record_counts_wins_losses_and_expectancy():
    summary = S.record_summary([_trade(10, 2.0), _trade(-5, -1.0), _trade(5, 1.0)])
    assert summary["closed_trades"] == 3
    assert summary["win_rate"] == round(2 / 3, 3)
    assert summary["avg_win_pct"] == 1.5
    assert summary["avg_loss_pct"] == -1.0
    assert summary["last_5"] == ["W +2.0%", "L -1.0%", "W +1.0%"]


def test_a_break_even_trade_counts_as_a_loss():
    assert S.record_summary([_trade(0.0, 0.0)])["win_rate"] == 0.0


def test_adapt_dial_waits_for_a_sample():
    dial = S.Dial(0.8, "base")
    assert S.adapt_dial(dial, {"closed_trades": 3, "win_rate": 0.0},
                        min_trades=10, win_floor=0.4).value == 0.8


def test_adapt_dial_leaves_a_good_record_alone():
    dial = S.Dial(0.8, "base")
    same = S.adapt_dial(dial, {"closed_trades": 20, "win_rate": 0.7},
                        min_trades=10, win_floor=0.4)
    assert same.value == 0.8


def test_adapt_dial_cuts_size_on_a_cold_record():
    dial = S.adapt_dial(S.Dial(0.8, "base"),
                        {"closed_trades": 20, "win_rate": 0.2},
                        min_trades=10, win_floor=0.4)
    assert dial.value == pytest.approx(0.4)
    assert "cold" in dial.reason
    assert dial.components["cold_cut"] == pytest.approx(0.5)


def test_adapt_dial_never_cuts_below_a_quarter():
    dial = S.adapt_dial(S.Dial(0.8, "base"),
                        {"closed_trades": 40, "win_rate": 0.0},
                        min_trades=10, win_floor=0.4)
    assert dial.value == pytest.approx(0.2)


def test_the_record_reaches_the_judge_as_finished_numbers():
    record = S.record_summary([_trade(1, 1.0), _trade(-1, -1.0)])
    facts = S.judge_evidence(crypto(), [], S.Dial(0.5, "x"), record=record)
    assert facts["record_closed_trades"] == 2
    assert facts["record_win_rate"] == 0.5
    assert facts["record_expectancy_pct"] == 0.0


def test_no_record_means_no_record_keys():
    facts = S.judge_evidence(crypto(), [], S.Dial(0.5, "x"))
    assert "record_closed_trades" not in facts
