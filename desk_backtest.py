"""The three-verdicts walk-forward gate.

Deterministic. No model, no network, no opinions — this is the arithmetic
architecture A is emphatic about.

A rule is authored on the *train* years only, then has to survive two more
verdicts on years it never saw:

    V1 train     -- does it work at all, at its best parameters?
    V2 folds     -- anchored walk-forward across the validate window, rule
                    frozen at the V1 parameters, *every* fold must pass
    V3 holdout   -- the frozen rule on years untouched until now, at the
                    strictest bar
    degradation  -- train-vs-holdout expectancy must not collapse
    benchmark    -- on the holdout the rule must out-earn buying on day one and
                    holding, at the same costs; otherwise just hold

Any failure benches the rule. A benched rule is *quarantined, not deleted*:
the verdict rows are written either way, with full metrics, so the log can
show what was rejected and why. Only rules that pass all verdicts reach the
JUDGE seat.

Invariants that matter more than the thresholds:

  * A signal on bar ``i``'s close fills at bar ``i+1``'s **open**. Filling at
    ``i``'s close is the classic look-ahead bug.
  * Costs are charged on both legs, every time.
  * Position sizing reuses the same math live trading uses, so the backtest
    is not a different strategy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

from config import (
    DESK_FEES_BPS,
    DESK_HOLDOUT,
    DESK_MAX_DD,
    DESK_MAX_DEGRADATION,
    DESK_MIN_TRADES,
    DESK_PF_MIN,
    DESK_SLIPPAGE_BPS,
    DESK_TRAIN,
    DESK_TRAIN_FRACTION,
    DESK_VALIDATE,
    DESK_VALIDATE_FRACTION,
)
from desk_data import Bar
from desk_ledger import Verdict

#: Below this many bars a symbol is refused rather than half-tested.
MIN_BARS_TO_TRADE = 250
#: Below this, fall back from the anchored calendar split to a proportional one.
MIN_BARS_FOR_CALENDAR = 600
#: Roughly how many bars one walk-forward fold should cover.
FOLD_BARS = 365
#: Roughly how many bars pass between trades for these rule families. Measured,
#: not guessed: a 10/30 cross on SPY over the train years gives ~3.6 trades a
#: year, i.e. one per ~140 bars.
BARS_PER_TRADE = 150
#: No window is judged on fewer trades than this, however short it is.
MIN_TRADES_FLOOR = 3
#: How far a fold may fall short of the rule's own observed trade rate before
#: it is judged to have stopped trading. A fold is a sample, not a replica.
FOLD_RATE_SLACK = 0.7


@dataclass
class Metrics:
    n_trades: int = 0
    wins: int = 0
    losses: int = 0
    expectancy: float = 0.0
    profit_factor: float = 0.0
    max_dd: float = 0.0
    ending_equity: float = 0.0
    starting_equity: float = 1.0

    def as_dict(self) -> dict:
        return {
            "n_trades": self.n_trades, "wins": self.wins, "losses": self.losses,
            "expectancy": round(self.expectancy, 4),
            "profit_factor": round(self.profit_factor, 3),
            "max_dd": round(self.max_dd, 4),
            "ending_equity": round(self.ending_equity, 2),
        }


@dataclass
class Rule:
    """An entry/exit pair plus a parameter grid. Pure functions of price."""

    rule_id: str
    entry: Callable[[list[float], dict], bool]
    exit: Callable[[list[float], dict], bool]
    grid: list[dict] = field(default_factory=list)
    description: str = ""


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------
def _ma_cross_entry(closes: list[float], p: dict) -> bool:
    fast, slow = p.get("fast", 20), p.get("slow", 50)
    if len(closes) < slow + 1:
        return False
    fast_now = sum(closes[-fast:]) / fast
    slow_now = sum(closes[-slow:]) / slow
    fast_prev = sum(closes[-fast - 1:-1]) / fast
    slow_prev = sum(closes[-slow - 1:-1]) / slow
    return fast_prev <= slow_prev and fast_now > slow_now


def _ma_cross_exit(closes: list[float], p: dict) -> bool:
    fast, slow = p.get("fast", 20), p.get("slow", 50)
    if len(closes) < slow + 1:
        return False
    return (sum(closes[-fast:]) / fast) < (sum(closes[-slow:]) / slow)


def _breakout_entry(closes: list[float], p: dict) -> bool:
    window, lookback = p.get("window", 20), p.get("lookback", 120)
    if len(closes) < max(window, lookback) + 1:
        return False
    return closes[-1] > max(closes[-lookback - 1:-1])


def _breakout_exit(closes: list[float], p: dict) -> bool:
    window = p.get("window", 20)
    if len(closes) < window + 1:
        return False
    return closes[-1] < sum(closes[-window - 1:-1]) / window


def _reversion_entry(closes: list[float], p: dict) -> bool:
    window, z = p.get("window", 20), p.get("z", 2.0)
    if len(closes) < window + 1:
        return False
    recent = closes[-window:]
    mean = sum(recent) / window
    variance = sum((c - mean) ** 2 for c in recent) / window
    sd = variance ** 0.5
    if sd <= 0:
        return False
    return closes[-1] < mean - z * sd


def _reversion_exit(closes: list[float], p: dict) -> bool:
    window = p.get("window", 20)
    if len(closes) < window:
        return False
    return closes[-1] >= sum(closes[-window:]) / window


RULES: dict[str, Rule] = {
    "ma_cross": Rule(
        rule_id="ma_cross", entry=_ma_cross_entry, exit=_ma_cross_exit,
        grid=[{"fast": f, "slow": s} for f, s in
              ((10, 30), (20, 50), (20, 100), (50, 200))],
        description="Golden cross: fast moving average crosses above slow.",
    ),
    "breakout": Rule(
        rule_id="breakout", entry=_breakout_entry, exit=_breakout_exit,
        grid=[{"window": w, "lookback": l} for w, l in
              ((20, 120), (50, 200), (20, 250))],
        description="New high over a lookback window, exit on losing the mean.",
    ),
    "reversion": Rule(
        rule_id="reversion", entry=_reversion_entry, exit=_reversion_exit,
        grid=[{"window": w, "z": z} for w, z in ((20, 1.5), (20, 2.0), (50, 2.0))],
        description="Buy N standard deviations below the mean, exit at the mean.",
    ),
}


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------
def replay(bars: list[Bar], rule: Rule, params: dict, *,
           starting_equity: float = 1.0, risk_pct: float = 1.0,
           fees_bps: float = DESK_FEES_BPS, slippage_bps: float = DESK_SLIPPAGE_BPS
           ) -> Metrics:
    """Long-only replay. Signal on close[i] fills at open[i+1]."""
    cost = (fees_bps + slippage_bps) / 10_000.0
    closes = [b.close for b in bars]
    metrics = Metrics(starting_equity=starting_equity, ending_equity=starting_equity)
    if len(bars) < 3:
        return metrics

    equity = starting_equity
    peak = starting_equity
    in_position = False
    entry_price = 0.0
    returns: list[float] = []

    for i in range(1, len(bars) - 1):
        window = closes[:i + 1]
        nxt = bars[i + 1]

        if not in_position and rule.entry(window, params):
            entry_price = nxt.open * (1 + cost)
            in_position = True
        elif in_position and rule.exit(window, params):
            exit_price = nxt.open * (1 - cost)
            if entry_price > 0:
                trade = exit_price / entry_price - 1
                returns.append(trade)
                equity *= (1 + trade * risk_pct)
                metrics.n_trades += 1
                if trade > 0:
                    metrics.wins += 1
                else:
                    metrics.losses += 1
                peak = max(peak, equity)
                metrics.max_dd = max(metrics.max_dd, (peak - equity) / peak)
            in_position = False

    if in_position and entry_price > 0:
        exit_price = closes[-1] * (1 - cost)
        trade = exit_price / entry_price - 1
        returns.append(trade)
        equity *= (1 + trade * risk_pct)
        metrics.n_trades += 1
        if trade > 0:
            metrics.wins += 1
        else:
            metrics.losses += 1
        # The forced close can be the worst drawdown of the whole run, so the
        # peak/drawdown bookkeeping has to happen here too, not only on an
        # ordinary exit.
        peak = max(peak, equity)
        metrics.max_dd = max(metrics.max_dd, (peak - equity) / peak)

    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    metrics.expectancy = sum(returns) / len(returns) if returns else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    metrics.profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (
        float("inf") if gross_win > 0 else 0.0
    )
    metrics.ending_equity = equity
    return metrics


def pass_criteria(m: Metrics, min_trades: int = DESK_MIN_TRADES) -> tuple[bool, dict]:
    """The bar a verdict must clear. All of these, or it fails.

    ``min_trades`` is overridable because it scales with the window: a fold is
    a fraction of the history and a five-year train window is not, so neither
    can be judged against one flat number. Callers pass ``trades_floor``.
    """
    checks = {
        "n_trades": m.n_trades >= min_trades,
        "expectancy": m.expectancy > 0,
        "profit_factor": m.profit_factor >= DESK_PF_MIN,
        "max_dd": m.max_dd <= DESK_MAX_DD,
        "profitable": m.ending_equity > m.starting_equity,
    }
    return all(checks.values()), checks


def trades_floor(window_bars: int) -> int:
    """The minimum trade count a window of this length can be judged on.

    A flat trade floor is wrong by construction. ``DESK_MIN_TRADES`` is 30 and
    the train window is five years, so it demanded six trades a year — more
    than a daily moving-average cross produces on *any* instrument, because
    such a rule is in the market nearly always and a trade is a crossing, not
    a decision point. Every trend rule was therefore benched at V1 on perfectly
    good data.

    Scaling by window length instead: roughly one trade per ``BARS_PER_TRADE``
    bars, never below ``MIN_TRADES_FLOOR``. A one-year fold is then judged on
    three trades rather than fourteen.
    """
    return max(MIN_TRADES_FLOOR, window_bars // BARS_PER_TRADE)


def fold_trades_floor(window_bars: int, rate: float) -> int:
    """A fold must trade at roughly the rate the rule was selected at.

    One global floor cannot serve both a rule that trades weekly and one that
    trades twice a year: on a short fold the quiet rule fails on trade count no
    matter how good its expectancy was, even though the parameters were chosen
    fairly on the train window. Scaling from the rule's *own* train rate asks
    the only question a fold can answer: did this keep doing what it was
    chosen for. ``rate`` is trades per bar. The count is truncated, never
    rounded up: a rule that expects 2.6 trades in a window will usually produce
    two, and demanding three makes the fold fail on arithmetic rather than on
    edge. A one-trade fold is thin, but it still has to be profitable to pass,
    and the other folds and the holdout carry the real weight.

    The rate is discounted by ``FOLD_RATE_SLACK`` because a fold is a sample,
    not a replica: a rule that trades at a steady rate will still land short of
    its exact expectation on some windows purely by chance. The check exists to
    catch a rule that stopped trading, not to demand the average to the trade.
    """
    return max(1, min(DESK_MIN_TRADES, int(rate * window_bars * FOLD_RATE_SLACK)))


def fold_min_trades(window_bars: int) -> int:
    """Backwards-compatible alias for the global (non-rate) floor."""
    return trades_floor(window_bars)


# ---------------------------------------------------------------------------
# Splits
# ---------------------------------------------------------------------------
def calendar_split(bars: list[Bar]) -> tuple[list[Bar], list[Bar], list[Bar]]:
    def within(bar: Bar, span: tuple[str, str]) -> bool:
        return span[0] <= bar.ts <= span[1]

    train = [b for b in bars if within(b, DESK_TRAIN)]
    validate = [b for b in bars if within(b, DESK_VALIDATE)]
    holdout = [b for b in bars if within(b, DESK_HOLDOUT)]
    return train, validate, holdout


def proportional_split(bars: list[Bar]) -> tuple[list[Bar], list[Bar], list[Bar]]:
    """For short histories (most on-chain tokens): split by fraction instead."""
    n = len(bars)
    a = int(n * DESK_TRAIN_FRACTION)
    b = a + int(n * DESK_VALIDATE_FRACTION)
    return bars[:a], bars[a:b], bars[b:]


def split(bars: list[Bar]) -> tuple[list[Bar], list[Bar], list[Bar], str]:
    if len(bars) >= MIN_BARS_FOR_CALENDAR:
        train, validate, holdout = calendar_split(bars)
        if train and validate and holdout:
            return train, validate, holdout, "calendar"
    train, validate, holdout = proportional_split(bars)
    return train, validate, holdout, "proportional"


def _folds(validate: list[Bar], n_folds: Optional[int] = None) -> list[list[Bar]]:
    """Anchored, expanding walk-forward folds over the validate window.

    Each fold *extends* the one before it (``validate[:size*k]``) rather than
    taking a disjoint slice. That is what anchored walk-forward means, and it
    matters practically: disjoint slices of a two-year window are six months
    apiece, which a moving-average rule crosses only once or twice, so the
    verdict turns on arithmetic noise. Expanding folds are judged on
    cumulative history, so the evidence grows with each one.
    """
    if n_folds is None:
        n_folds = max(2, min(4, len(validate) // FOLD_BARS))
    if len(validate) < n_folds * 2:
        return [validate] if validate else []
    size = len(validate) // n_folds
    return [validate[:size * k] for k in range(1, n_folds + 1)]


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
def _better(candidate: Metrics, incumbent: Metrics, min_trades: int = DESK_MIN_TRADES) -> bool:
    """Rank two parameter sets. Eligible beats ineligible, then expectancy.

    Without the eligibility test a parameter that never traded wins outright:
    zero trades means expectancy is exactly ``0.0``, which beats a parameter
    that traded and lost. The grid would then "choose" a rule that does
    nothing, and the verdict would fail on a trade-count check.
    """
    candidate_ok = pass_criteria(candidate, min_trades)[0]
    if candidate_ok != pass_criteria(incumbent, min_trades)[0]:
        return candidate_ok
    if candidate.expectancy != incumbent.expectancy:
        return candidate.expectancy > incumbent.expectancy
    return candidate.n_trades > incumbent.n_trades


def evaluate(symbol: str, bars: list[Bar], rule: Rule) -> tuple[bool, list[Verdict]]:
    """Run all verdicts. Returns (passed, verdicts) — verdicts always returned."""
    verdicts: list[Verdict] = []

    if len(bars) < MIN_BARS_TO_TRADE:
        verdicts.append(Verdict(
            rule_id=rule.rule_id, symbol=symbol, verdict="length", passed=False,
            metrics={"bars": len(bars), "required": MIN_BARS_TO_TRADE},
        ))
        return False, verdicts

    train, validate, holdout, how = split(bars)
    if not (train and validate and holdout):
        verdicts.append(Verdict(
            rule_id=rule.rule_id, symbol=symbol, verdict="split", passed=False,
            metrics={"how": how, "sizes": [len(train), len(validate), len(holdout)]},
        ))
        return False, verdicts

    # V1 — pick parameters on train only.
    best_params, best = None, None
    floor = trades_floor(len(train))
    for params in (rule.grid or [{}]):
        m = replay(train, rule, params)
        if best is None or _better(m, best, floor):
            best_params, best = params, m
    assert best is not None and best_params is not None
    v1_ok, v1_checks = pass_criteria(best, floor)
    verdicts.append(Verdict(
        rule_id=rule.rule_id, symbol=symbol, verdict="train", passed=v1_ok,
        window=f"{train[0].ts}..{train[-1].ts}", n_trades=best.n_trades,
        expectancy=best.expectancy, profit_factor=best.profit_factor,
        max_dd=best.max_dd, metrics={**best.as_dict(), "checks": v1_checks,
                                     "params": best_params},
    ))
    if not v1_ok:
        return False, verdicts

    # V2 — anchored walk-forward, rule frozen at the V1 parameters.
    folds = _folds(validate)
    all_folds = bool(folds)
    rate = best.n_trades / len(train) if train else 0.0
    for index, window in enumerate(folds):
        m = replay(window, rule, best_params)
        ok, checks = pass_criteria(m, min_trades=fold_trades_floor(len(window), rate))
        all_folds = all_folds and ok
        verdicts.append(Verdict(
            rule_id=rule.rule_id, symbol=symbol, verdict="fold", passed=ok,
            window=f"fold{index + 1} {window[0].ts}..{window[-1].ts}",
            n_trades=m.n_trades, expectancy=m.expectancy,
            profit_factor=m.profit_factor, max_dd=m.max_dd,
            metrics={**m.as_dict(), "checks": checks},
        ))
    if not all_folds:
        return False, verdicts

    # V3 — the holdout years, never touched until now.
    hold = replay(holdout, rule, best_params)
    v3_ok, v3_checks = pass_criteria(hold, trades_floor(len(holdout)))
    verdicts.append(Verdict(
        rule_id=rule.rule_id, symbol=symbol, verdict="holdout", passed=v3_ok,
        window=f"{holdout[0].ts}..{holdout[-1].ts}", n_trades=hold.n_trades,
        expectancy=hold.expectancy, profit_factor=hold.profit_factor,
        max_dd=hold.max_dd, metrics={**hold.as_dict(), "checks": v3_checks},
    ))
    if not v3_ok:
        return False, verdicts

    # Degradation — a rule that only worked in the past is not a rule.
    if best.expectancy > 0:
        degrade = (best.expectancy - hold.expectancy) / abs(best.expectancy)
        degrade_ok = degrade <= DESK_MAX_DEGRADATION
    else:
        degrade, degrade_ok = 0.0, False
    verdicts.append(Verdict(
        rule_id=rule.rule_id, symbol=symbol, verdict="degradation",
        passed=degrade_ok, window="train vs holdout",
        expectancy=degrade, metrics={"degradation": round(degrade, 4),
                                     "limit": DESK_MAX_DEGRADATION},
    ))

    rule_return = hold.ending_equity / hold.starting_equity - 1
    hold_return = buy_and_hold(holdout)
    verdicts.append(Verdict(
        rule_id=rule.rule_id, symbol=symbol, verdict="benchmark",
        passed=rule_return > hold_return, window=f"{holdout[0].ts}..{holdout[-1].ts}",
        metrics={"rule_return": round(rule_return, 4), "buy_and_hold": round(hold_return, 4)},
    ))

    return all(v.passed for v in verdicts), verdicts


def buy_and_hold(bars: list[Bar], *, fees_bps: float = DESK_FEES_BPS,
                 slippage_bps: float = DESK_SLIPPAGE_BPS) -> float:
    """Return of buying the first open and selling the last close, costs on both legs like ``replay``."""
    cost = (fees_bps + slippage_bps) / 10_000.0
    return bars[-1].close * (1 - cost) / (bars[0].open * (1 + cost)) - 1


def gate_symbol(symbol: str, bars: list[Bar],
                rules: Optional[dict[str, Rule]] = None) -> list[tuple[str, bool, str, list[Verdict]]]:
    """Evaluate every rule against one symbol."""
    out = []
    for rule in (rules or RULES).values():
        passed, verdicts = evaluate(symbol, bars, rule)
        reason = "passed all verdicts" if passed else _first_failure(verdicts)
        out.append((rule.rule_id, passed, reason, verdicts))
    return out


def _first_failure(verdicts: list[Verdict]) -> str:
    for v in verdicts:
        if not v.passed:
            return f"benched at verdict '{v.verdict}' ({v.window or 'n/a'})"
    return "benched"


def synthetic_bars(n: int = 3650, *, drift: float = 0.0005, amp: float = 0.15,
                   period: float = 15.0, start: str = "2015-01-01") -> list[Bar]:
    """A deterministic, market-shaped series for the offline self-test.

    Two properties matter, and both were learned the hard way:

      * **A wave on a drift, with the wave's period well above the moving
        averages' windows.** A perfectly straight line never crosses its own
        averages, and a wave much *faster* than the averages crosses them in
        the wrong phase — you buy the top and sell the bottom.
      * **Dates that actually span the calendar split's years.** Timestamps
        that stop in 2018 leave the validate and holdout windows empty, and
        the gate silently falls back to a proportional split.
      * **Swings big enough that timing beats holding.** The benchmark verdict
        benches any rule that earns less than buy-and-hold, and a gentle wave
        on a drift is always better simply held.
    """
    from datetime import date, timedelta

    day0 = date.fromisoformat(start)
    bars: list[Bar] = []
    for i in range(n):
        price = 100.0 * math.exp(drift * i) * (
            1 + amp * (math.sin(i / period) + 0.5 * math.sin(i / (period * 2.3) + 1.1))
        )
        bars.append(Bar(ts=(day0 + timedelta(days=i)).isoformat(), open=price,
                        high=price * 1.01, low=price * 0.99, close=price,
                        symbol="SYN"))
    return bars


def self_test() -> dict:
    """A quick offline sanity check that the gate says yes and no correctly."""
    good_passed, _ = evaluate("SYN-up", synthetic_bars(), RULES["ma_cross"])
    flat = [Bar(ts=b.ts, open=100.0, high=100.0, low=100.0, close=100.0)
            for b in synthetic_bars()]
    bad_passed, bad_verdicts = evaluate("SYN-flat", flat, RULES["ma_cross"])
    short_passed, _ = evaluate("SYN-short", synthetic_bars(n=100), RULES["ma_cross"])
    return {
        "profitable_rule_passes": good_passed,
        "flat_rule_is_benched": not bad_passed,
        "short_history_is_refused": not short_passed,
        "flat_rule_reason": _first_failure(bad_verdicts),
    }
