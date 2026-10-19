"""The seats, and the wall between them.

One rule decides the whole shape of this file:

    A seat that researches never places an order.
    A seat that places orders never gets an opinion.

That is not a slogan — :data:`SEATS` carries ``places_orders`` and
``has_opinion`` as data and :func:`assert_separation` enforces the XOR at
import time. If someone later gives SIZE a model call, the import fails and
they find out immediately rather than by wondering why sizing is inconsistent.

Everything here is a plain function with an explicit input and output. The
only seat that touches a model is JUDGE, and it is handed numbers that are
already computed so it never does arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from config import (
    DESK_CONFIDENCE_FLOOR,
    DESK_DAILY_LOSS_LIMIT_PCT,
    DESK_LIQUIDITY_FLOOR,
    DESK_MAX_DRAWDOWN_PCT,
    DESK_MAX_LOSING_DAYS,
    DESK_MAX_POSITION_PCT,
    DESK_MAX_POSITIONS,
    DESK_MIN_BARS,
    DESK_REFERENCE_ATR_PCT,
)
from desk_data import Bar, Candidate
from desk_ledger import Decision, Position

#: The daily range a "normal" instrument moves, as a fraction. Sizing and stops
#: are expressed relative to it, so an unknown symbol is treated as ordinary
#: rather than as either riskless or untradable.
DEFAULT_ATR_PCT = DESK_REFERENCE_ATR_PCT / 100.0


@dataclass(frozen=True)
class Seat:
    """A desk seat and its powers. Read this table to know who may do what."""

    name: str
    kind: str  # "code" | "model"
    places_orders: bool
    has_opinion: bool
    does: str


SEATS: dict[str, Seat] = {
    "chief": Seat("chief", "code", False, False,
                  "allocates budget across seats; pure arithmetic"),
    "quant": Seat("quant", "code", False, False,
                  "emits the daily size dial; never chooses what to buy"),
    "scan": Seat("scan", "code", False, False,
                  "finds candidates; never judges or fills"),
    "judge": Seat("judge", "model", False, True,
                  "the only seat with an opinion; never sizes, never fills"),
    "size": Seat("size", "code", False, False,
                 "turns budget x dial into a quantity; no opinion"),
    "fills": Seat("fills", "code", True, False,
                  "places orders; never gets an opinion"),
    "risk": Seat("risk", "code", False, False,
                 "vetoes and trips the kill switch; pure arithmetic"),
}


def assert_separation() -> None:
    """Research XOR trading. Called at import so drift is loud."""
    for seat in SEATS.values():
        if seat.places_orders and seat.has_opinion:
            raise AssertionError(
                f"seat '{seat.name}' both places orders and has an opinion"
            )
    if not any(s.places_orders for s in SEATS.values()):
        raise AssertionError("no seat can place an order")
    if sum(1 for s in SEATS.values() if s.has_opinion) != 1:
        raise AssertionError("exactly one seat may hold an opinion")


assert_separation()


# ---------------------------------------------------------------------------
# CHIEF + QUANT — the daily dial. Deterministic, refreshed once per day.
# ---------------------------------------------------------------------------
@dataclass
class Dial:
    """The first and largest size multiplier. Clamped 0.0-1.0 by construction."""

    value: float = 0.0
    reason: str = ""
    components: dict[str, float] = field(default_factory=dict)


def _sma(values: list[float], window: int) -> Optional[float]:
    if window <= 0 or len(values) < window:
        return None
    return sum(values[-window:]) / window


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5


def compute_dial(bars: list[Bar]) -> Dial:
    """How much rope the market is giving us today, as one number in 0..1.

    Deterministic on purpose: three reads of the same bars must agree, and the
    desk must be explainable. Trend and long-term slope push the dial up;
    realised volatility pulls it down. Thin history means a cautious dial.
    """
    closes = [b.close for b in bars]
    if len(closes) < 60:
        return Dial(0.25, f"only {len(closes)} bars — half-size until proven")

    fast, mid, slow = _sma(closes, 10), _sma(closes, 50), _sma(closes, 200) or _sma(closes, 100)
    if not fast or not mid or not slow:
        return Dial(0.3, "moving averages unavailable — cautious")

    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]
    vol = _stdev(returns[-20:]) if len(returns) >= 20 else 0.02

    trend = max(-0.3, min(0.3, (fast / mid - 1) * 5))
    slope = max(-0.2, min(0.2, (mid / slow - 1) * 2))
    vol_penalty = -min(0.3, (vol / 0.04) * 0.15) if vol > 0 else 0.0

    raw = 0.5 + trend + slope + vol_penalty
    value = max(0.0, min(1.0, raw))
    return Dial(
        value=round(value, 3),
        reason=(f"trend {trend:+.2f}, slope {slope:+.2f}, "
                f"vol {vol:.3f} penalty {vol_penalty:+.2f}"),
        components={"trend": round(trend, 4), "slope": round(slope, 4),
                    "vol": round(vol, 5), "vol_penalty": round(vol_penalty, 4)},
    )


def record_summary(trades: list[dict]) -> dict[str, Any]:
    """How the desk has actually done, as finished numbers for the judge.

    The judge otherwise sees one candidate in isolation and cannot tell whether
    the desk is in a hot or a cold patch. This is the feedback edge: outcomes
    go back into the next decision as facts, never as an opinion.
    """
    if not trades:
        return {"closed_trades": 0, "win_rate": None, "avg_win_pct": None,
                "avg_loss_pct": None, "expectancy_pct": None, "last_5": []}
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    n = len(trades)
    return {
        "closed_trades": n,
        "win_rate": round(len(wins) / n, 3),
        "avg_win_pct": round(sum(t["pnl_pct"] for t in wins) / len(wins), 2) if wins else None,
        "avg_loss_pct": round(sum(t["pnl_pct"] for t in losses) / len(losses), 2) if losses else None,
        "expectancy_pct": round(sum(t["pnl_pct"] for t in trades) / n, 3),
        "last_5": [f"{'W' if t['pnl'] > 0 else 'L'} {t['pnl_pct']:+.1f}%" for t in trades[-5:]],
    }


def adapt_dial(dial: Dial, record: dict[str, Any], *, min_trades: int,
               win_floor: float) -> Dial:
    """Shrink the dial while the desk is losing, restore it when it is not.

    Deliberately one-sided and crude: it can only ever cut size, never add it,
    so a bad patch is handled without a model and a good patch is still capped
    by the dial's own ceiling. Needs ``min_trades`` before it will act at all.
    """
    rate = record.get("win_rate")
    if rate is None or record.get("closed_trades", 0) < min_trades:
        return dial
    if rate >= win_floor:
        return dial
    cut = max(0.25, rate / win_floor)
    return Dial(value=round(dial.value * cut, 3),
                reason=(dial.reason + f" | cold: {rate:.0%} win over "
                        f"{record['closed_trades']} trades"),
                components={**dial.components, "cold_cut": round(cut, 3)})


@dataclass
class Budget:
    """What CHIEF hands the rest of the desk for this cycle."""

    equity: float
    cash: float
    per_position: float
    slots: int
    dial: Dial


def chief_budget(equity: float, cash: float, dial: Dial,
                 open_count: int) -> Budget:
    """Split capital into per-position budget. Pure arithmetic, no judgement.

    The risk ceiling is applied here, not by a model: a single position can
    never exceed ``DESK_MAX_POSITION_PCT`` of equity, scaled by the dial.
    """
    pct = max(0.0, DESK_MAX_POSITION_PCT) / 100.0
    ceiling = equity * pct
    per_position = max(0.0, min(ceiling, cash) * dial.value)
    slots = max(0, DESK_MAX_POSITIONS - open_count)
    return Budget(equity=equity, cash=cash, per_position=per_position,
                  slots=slots, dial=dial)


# ---------------------------------------------------------------------------
# SCAN — finds candidates. No opinion, no orders.
# ---------------------------------------------------------------------------
def scan(venue: str, limit: int, source: Any = None) -> list[Candidate]:
    """Candidates for one venue. Never raises: a dead source yields []."""
    from desk_data import DataUnavailable, source_for

    src = source or source_for(venue)
    try:
        if venue == "crypto":
            found = src.trending(limit=limit, min_liquidity=DESK_LIQUIDITY_FLOOR)
        else:
            found = scan_universe(src, limit)
    except DataUnavailable:
        return []
    except Exception:  # noqa: BLE001 — a scan must never break a cycle
        return []
    return list(found)[:limit]


def scan_universe(source: Any, limit: int) -> list[Candidate]:
    """Walk ``DESK_UNIVERSE`` for the stocks venue.

    Yahoo has no "trending" endpoint, so a fixed universe is the honest
    equivalent. Crypto pairs (``-USD``) are left to DexScreener. Bar counts are
    stashed on the candidate so the history gate can see them without a
    second fetch.
    """
    from config import DESK_UNIVERSE
    from desk_data import DataUnavailable, _days_ago

    out: list[Candidate] = []
    for symbol in DESK_UNIVERSE:
        if symbol.endswith("-USD"):
            continue
        try:
            bars = source.bars(symbol, start=_days_ago(400))
        except DataUnavailable:
            continue
        if len(bars) < 2:
            continue
        last, prev = bars[-1], bars[-2]
        change = (last.close / prev.close - 1) * 100 if prev.close else 0.0
        cand = Candidate(
            symbol=symbol, venue="stocks", source=getattr(source, "name", "yahoo"),
            price=last.close, liquidity=0.0, vol_24h=last.volume * last.close,
            change_24h=change, raw={"bars_count": len(bars)},
        )
        cand.score = score_candidate(cand)
        out.append(cand)
    return sorted(out, key=lambda c: c.score, reverse=True)[:limit]


def bars_for(cand: Candidate, source: Any = None) -> list[Bar]:
    """Daily bars for a candidate, or [] when the symbol simply has none.

    Crypto tokens usually do; a brand-new memecoin usually does not. An empty
    list is a legitimate answer, not an error — the history gate reads it.
    """
    from desk_data import DataUnavailable, YahooSource, source_for

    src = source or source_for(cand.venue)
    try:
        if cand.venue == "crypto":
            return YahooSource().bars(f"{cand.symbol}-USD", start="2015-01-01")
    except DataUnavailable:
        return []
    try:
        return src.bars(cand.symbol, start="2015-01-01")
    except DataUnavailable:
        return []


def score_candidate(cand: Candidate) -> float:
    """A cheap, deterministic ranking number. Not a decision — just an order."""
    if cand.price <= 0:
        return 0.0
    turnover = cand.vol_24h / max(cand.liquidity, 1.0)
    momentum = max(-1.0, min(1.0, cand.change_24h / 50.0))
    return round(min(turnover, 5.0) * 0.5 + momentum * 0.5, 4)


# ---------------------------------------------------------------------------
# RISK + gates — deterministic vetoes. Applied before and after the judge.
# ---------------------------------------------------------------------------
@dataclass
class GateResult:
    ok: bool
    reason: str = ""
    check: str = ""


def gate_candidate(cand: Candidate, *, bars_count: int = 0,
                   already_held: bool = False, slots: int = 1,
                   cooldown: bool = False) -> GateResult:
    """Every hard floor, checked in order. First failure wins and is named."""
    if cand.price <= 0:
        return GateResult(False, "no price", "price")
    if already_held:
        return GateResult(False, "already holding", "held")
    if slots <= 0:
        return GateResult(False, "no free slots", "slots")
    if cooldown:
        return GateResult(False, "cooldown after recent fill", "cooldown")
    if cand.venue == "crypto":
        if cand.liquidity < DESK_LIQUIDITY_FLOOR:
            return GateResult(
                False,
                f"liquidity {cand.liquidity:,.0f} < floor {DESK_LIQUIDITY_FLOOR:,.0f}",
                "liquidity",
            )
    if bars_count and bars_count < DESK_MIN_BARS:
        return GateResult(False, f"only {bars_count} bars", "history")
    return GateResult(True, "cleared", "gate")


def gate_decision(decision: Decision) -> GateResult:
    """Applied *after* the judge. The model does not get to override a floor."""
    if decision.action != "buy":
        return GateResult(False, f"judge chose {decision.action}", "judge")
    if decision.confidence < DESK_CONFIDENCE_FLOOR:
        return GateResult(
            False,
            f"confidence {decision.confidence:.2f} < floor {DESK_CONFIDENCE_FLOOR:.2f}",
            "confidence",
        )
    return GateResult(True, "cleared", "confidence")


@dataclass
class KillSwitch:
    tripped: bool
    reason: str = ""


def risk_check(*, day_pnl_pct: float, drawdown_pct: float,
               losing_days: int) -> KillSwitch:
    """Three ceilings. Any one breach trips the switch and the desk flattens."""
    if day_pnl_pct <= -abs(DESK_DAILY_LOSS_LIMIT_PCT):
        return KillSwitch(True, f"daily loss {day_pnl_pct:.2f}% "
                                f"<= -{DESK_DAILY_LOSS_LIMIT_PCT:.2f}%")
    if drawdown_pct >= abs(DESK_MAX_DRAWDOWN_PCT):
        return KillSwitch(True, f"drawdown {drawdown_pct:.2f}% "
                                f">= {DESK_MAX_DRAWDOWN_PCT:.2f}%")
    if losing_days >= DESK_MAX_LOSING_DAYS:
        return KillSwitch(True, f"{losing_days} consecutive losing days")
    return KillSwitch(False, "within limits")


# ---------------------------------------------------------------------------
# JUDGE — the one opinion. Given facts, not raw data.
# ---------------------------------------------------------------------------
def judge_evidence(cand: Candidate, bars: list[Bar], dial: Dial, *,
                   gate_history: int = 0, gate_note: str = "",
                   record: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Pre-compute everything the judge might want to reason about.

    Every value is a finished number. If the judge has to divide anything, the
    arithmetic has leaked into the model and this function has failed.
    """
    closes = [b.close for b in bars] if bars else []
    recent = closes[-20:]
    mean20 = sum(recent) / len(recent) if recent else 0.0
    high = max(closes[-120:]) if len(closes) >= 120 else (max(closes) if closes else 0.0)
    low = min(closes[-120:]) if len(closes) >= 120 else (min(closes) if closes else 0.0)
    range_pct = atr_pct(bars) if bars else DEFAULT_ATR_PCT

    facts = {
        "symbol": cand.symbol,
        "venue": cand.venue,
        "price": round(cand.price, 8) if cand.price < 1 else round(cand.price, 4),
        "liquidity_usd": round(cand.liquidity),
        "volume_24h_usd": round(cand.vol_24h),
        "change_24h_pct": round(cand.change_24h, 2),
        "bars_of_history": len(bars),
        "pct_vs_20day_mean": round((cand.price / mean20 - 1) * 100, 2) if mean20 else 0.0,
        "pct_vs_120day_high": round((cand.price / high - 1) * 100, 2) if high else 0.0,
        "pct_vs_120day_low": round((cand.price / low - 1) * 100, 2) if low else 0.0,
        "daily_dial_0_to_1": dial.value,
        "walk_forward_verdict": gate_note or "not run",
        "rules_benched_by_gate": gate_history,
        # How violently this name moves, and what that costs: the stop is
        # placed 2 ranges down, so the percentage is the honest "how wrong can
        # I be before this is a loss" the judge is choosing between.
        "daily_range_pct": round(range_pct * 100, 2),
        "stop_distance_pct": round(range_pct * 200, 2),
    }
    if record:
        facts["record_closed_trades"] = record.get("closed_trades", 0)
        facts["record_win_rate"] = record.get("win_rate")
        facts["record_avg_win_pct"] = record.get("avg_win_pct")
        facts["record_avg_loss_pct"] = record.get("avg_loss_pct")
        facts["record_expectancy_pct"] = record.get("expectancy_pct")
        facts["record_last_5"] = record.get("last_5", [])
    return facts


def judge(cand: Candidate, facts: dict[str, Any], brain: Any,
          candidate_id: Optional[int] = None) -> Decision:
    """The only model call in the desk. Returns a Decision, skips included."""
    decision = brain.decide(facts, symbol=cand.symbol)
    decision.candidate_id = candidate_id
    return decision


# ---------------------------------------------------------------------------
# SIZE — budget x dial into a quantity. No opinion.
# ---------------------------------------------------------------------------
def atr_pct(bars: list[Bar], window: int = 14) -> float:
    """Average true range as a fraction of price. Deterministic, never raises.

    True range is the day's high-low *and* the gap from the previous close,
    which is what makes it a measure of noise rather than of direction. A thin
    symbol falls back to the reference range instead of being sized as if it
    were calm.
    """
    usable = [b for b in bars if b.close > 0]
    if len(usable) < 2:
        return DEFAULT_ATR_PCT
    ranges = []
    for prev, bar in zip(usable, usable[1:]):
        ranges.append(max(bar.high - bar.low,
                          abs(bar.high - prev.close),
                          abs(bar.low - prev.close)))
    recent = ranges[-window:]
    mean = sum(recent) / len(recent)
    return max(0.005, min(0.25, mean / usable[-1].close)) if mean else DEFAULT_ATR_PCT


def size_position(budget: float, price: float, confidence: float,
                  atr_pct: float = DEFAULT_ATR_PCT) -> float:
    """Quantity from a cash budget, cut back for a noisy name.

    Confidence nudges within the budget, then the budget is scaled by how far
    this name's daily range sits from the reference. Because the stop is also a
    multiple of the range, the cash *at risk* comes out flat across names
    noisier than the reference — the same bad day costs the same dollars
    whatever the ticker. The scale only ever shrinks, so a quieter name risks
    less than that target rather than spending past its budget.
    """
    if price <= 0 or budget <= 0:
        return 0.0
    scale = max(0.25, min(1.0, DEFAULT_ATR_PCT / max(atr_pct, 1e-9)))
    scaled = budget * max(0.0, min(1.0, confidence)) * scale
    return round(scaled / price, 8)


def stop_and_take(price: float, atr_pct: float = DEFAULT_ATR_PCT) -> tuple[float, float]:
    """Stop and target as multiples of the name's own range.

    A 4% stop that suits an index stops a meme coin out on an ordinary day; a
    fixed 4% on a token that swings 15% is noise. Scaling both legs by the
    range keeps the reward/risk ratio fixed (2:3) while the *distance* fits the
    instrument. Deterministic so the ledger stays comparable.
    """
    atr_pct = max(0.005, min(0.25, atr_pct))
    return round(price * (1 - 2 * atr_pct), 8), round(price * (1 + 3 * atr_pct), 8)


def position_from_fill(symbol: str, qty: float, price: float, venue: str,
                       mode: str, atr_pct: float = DEFAULT_ATR_PCT) -> Position:
    stop, take = stop_and_take(price, atr_pct)
    return Position(symbol=symbol, qty=qty, avg_price=price, venue=venue,
                    stop=stop, take=take, atr_pct=atr_pct, mode=mode)


def trail_stop(position: Position, price: float,
               atr_pct: float = DEFAULT_ATR_PCT) -> float:
    """The stop this position *should* carry now. Only ever ratchets up.

    A fixed stop plus a fixed target gives back the whole open gain on a spike
    that reverses. Two deterministic rungs fix that without an opinion: once a
    position is up a full stop-distance the stop moves to entry (never risk a
    winner again), and past that it trails a stop-distance under the mark.
    Never returns a stop below the one already held.
    """
    current = position.stop or 0.0
    if price <= 0 or position.avg_price <= 0:
        return current
    atr_pct = max(0.005, min(0.25, atr_pct))
    distance = 2 * atr_pct
    if price >= position.avg_price * (1 + distance):
        current = max(current, position.avg_price)          # breakeven
    current = max(current, price * (1 - distance))          # trail
    return round(current, 8)


def should_exit(position: Position, price: float) -> Optional[str]:
    """Stop or take-profit hit? Deterministic, checked by the desk each cycle."""
    if position.stop and price <= position.stop:
        return f"stop hit at {price:g} (limit {position.stop:g})"
    if position.take and price >= position.take:
        return f"target hit at {price:g} (target {position.take:g})"
    return None


def self_test() -> dict:
    """Assert the wall holds and the dial reacts the way it should."""
    assert_separation()
    rising = [Bar(ts=f"d{i}", open=100 + i, high=0, low=0, close=100 + i) for i in range(260)]
    falling = [Bar(ts=f"d{i}", open=200 - i, high=0, low=0, close=200 - i) for i in range(260)]
    up, down = compute_dial(rising), compute_dial(falling)
    return {
        "seats": len(SEATS),
        "one_opinion": sum(1 for s in SEATS.values() if s.has_opinion) == 1,
        "riser_dial": up.value,
        "faller_dial": down.value,
        "dial_reacts": up.value > down.value,
        "thin_history_is_cautious": compute_dial(rising[:10]).value <= 0.3,
    }
