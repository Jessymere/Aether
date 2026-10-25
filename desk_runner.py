"""The runner: one cycle of the desk, plus the kill switch.

Order per cycle is fixed and readable:

    exits  ->  scan  ->  gate  ->  walk-forward gate  ->  judge  ->  gate
           ->  size  ->  fill  ->  ledger  ->  risk

Exits run first so a stop that fires frees both a slot and the cash for the
same cycle's entries. RISK runs last and can halt everything.

Two things are deliberate:

  * **Every path writes a decision row, including the ones that never reach
    the judge.** A gate rejection is recorded as a skip with ``vetoed_by`` set
    to the rule that stopped it, so the ledger can answer "what did it say no
    to, and why" without replaying the code.
  * **The walk-forward gate is memoised per (symbol, rule) for the day.**
    Replaying 2,500 bars for every candidate every cycle would make the UI
    feel broken; the verdict for a symbol does not change intraday.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Optional

from config import (
    DESK_DIAL_FILE,
    DESK_EXECUTION_MODE,
    DESK_KILL_FILE,
    DESK_LOGS,
    DESK_RECORD_MIN_TRADES,
    DESK_RECORD_WINDOW,
    DESK_RECORD_WIN_FLOOR,
    DESK_MAX_PER_SECTOR,
    DESK_SECTORS,
    DESK_START_CAPITAL,
    desk_costs,
)
from desk_alerts import notify
from desk_backtest import RULES
from desk_broker import Order, PaperBroker, make_broker
from desk_data import Candidate, DataUnavailable
from desk_ledger import (
    Decision,
    closed_trades,
    connect,
    delete_position,
    fills_for,
    mint_map,
    now,
    open_positions,
    record_candidate,
    record_decision,
    record_equity,
    record_fill,
    record_run,
    equity_history,
    record_verdict,
    rule_records,
    set_candidate_rule,
    set_candidate_status,
    upsert_position,
)
from desk_seats import (
    adapt_dial,
    atr_pct,
    bars_for,
    chief_budget,
    compute_dial,
    gate_candidate,
    gate_decision,
    judge,
    judge_evidence,
    position_from_fill,
    record_summary,
    risk_check,
    risk_inputs,
    scan,
    score_candidate,
    should_exit,
    size_position,
    trail_stop,
)

DEFAULT_VENUES = ("stocks", "crypto")
#: How many candidates per venue enter the (expensive) walk-forward gate.
GATE_LIMIT = 12
#: Daily bars barely move intraday, so a cycle re-reads them rather than the
#: wire. Five minutes keeps the history gate honest without hammering Yahoo.
BAR_CACHE_SECONDS = 300
#: The dial is a daily number and costs a year of benchmark bars to compute.
DIAL_CACHE_SECONDS = 3600
#: Enough calendar days for the dial's 200-day average (~290 trading days),
#: plus a margin for holidays and outages.
DIAL_LOOKBACK_DAYS = 420
#: A price is worth 20 seconds. The exit loop and the snapshot both mark the
#: same symbol inside one cycle.
MARK_CACHE_SECONDS = 20
#: A judged symbol is not re-judged for this long. The judge is the only model
#: call in the cycle and a repeat verdict on unchanged bars buys nothing.
JUDGE_CACHE_SECONDS = 900


def _num(value: Any, fallback: float = 0.0) -> float:
    """A position field may be None (e.g. a broker that sets no stops)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _row(position, mark: Optional[float] = None) -> dict:
    """One position as the UI sees it, with unrealised P&L when marked.

    An unmarked position reports ``None`` rather than 0.0, so "no price yet"
    cannot be mistaken for "flat".
    """
    row = {"symbol": position.symbol, "qty": position.qty,
           "avg_price": position.avg_price, "stop": position.stop,
           "take": position.take, "venue": position.venue, "mode": position.mode,
           "mark": mark, "pnl": None, "pnl_pct": None}
    if mark and position.avg_price > 0:
        pnl = (mark - position.avg_price) * position.qty
        row["pnl"] = round(pnl, 4)
        row["pnl_pct"] = round((mark / position.avg_price - 1) * 100, 2)
    return row


@dataclass
class DeskResult:
    """What the front ends render. Mirrors OrchestrationResult's role."""

    mode: str = "desk"
    venues: list[str] = field(default_factory=list)
    cycles: int = 0
    scanned: int = 0
    gated: int = 0
    judged: int = 0
    filled: int = 0
    skips: int = 0
    equity: float = 0.0
    start_equity: float = 0.0
    positions: list[dict] = field(default_factory=list)
    dial: float = 0.0
    kill_switch: bool = False
    kill_reason: str = ""
    report: str = ""
    log: list[str] = field(default_factory=list)
    ledger: str = ""

    @property
    def pnl(self) -> float:
        return self.equity - self.start_equity


class DeskRunner:
    """Drives the desk. The UI owns the thread; this owns the cycle."""

    def __init__(self, venues: Optional[list[str]] = None,
                 mode: Optional[str] = None, *,
                 brain: Any = None,
                 status_cb: Optional[Callable] = None,
                 log_cb: Optional[Callable] = None,
                 cancel_cb: Optional[Callable] = None,
                 starting_cash: float = DESK_START_CAPITAL,
                 scan_limit: int = 12) -> None:
        self.venues = list(venues or DEFAULT_VENUES)
        self.requested_mode = (mode or DESK_EXECUTION_MODE or "paper").lower()
        self.brain = brain
        self.status_cb = status_cb
        self.log_cb = log_cb
        self.cancel_cb = cancel_cb
        self.scan_limit = scan_limit
        self.starting_cash = starting_cash

        self.conn = connect()
        self._brokers: dict[str, Any] = {}
        self._verdict_cache: dict[tuple[str, str, tuple[str, ...]], tuple[bool, str]] = {}
        self._recent_fills: dict[str, int] = {}
        #: symbol -> Solana mint, learned from scan and kept in the ledger. A
        #: live sell of a restored position has no candidate in hand, so the
        #: address has to outlive the cycle that found it.
        self._mints: dict[str, str] = mint_map(self.conn)
        #: The UI's window on what the desk thought. The ledger is the record;
        #: this is only the recent tail of it, so the table cannot grow forever.
        self._recent: deque = deque(maxlen=60)
        #: Last observed price per symbol, so the snapshot can show P&L without
        #: another network call on the UI thread.
        self._marks: dict[str, float] = {}
        self._bars_cache: dict[str, tuple[float, list]] = {}
        self._dial_cache: dict[str, tuple[float, Any]] = {}
        self._mark_cache: dict[str, tuple[float, float]] = {}
        #: symbol -> (monotonic, Decision). A repeated verdict on unchanged
        #: daily bars is the same verdict, so the model call is skipped.
        self._judge_cache: dict[str, tuple[float, Decision]] = {}
        #: venue -> last record summary. Held so the poll does not re-walk the
        #: fills table on the UI thread; refreshed once per cycle.
        self._records: dict[str, dict[str, Any]] = {}
        #: venue -> {rule_id: summary} for rules benched by their own closed
        #: record. Refreshed once per cycle; a rule's own losing trades are a
        #: narrower reason to stand it down than a cold venue.
        self._rule_blocks: dict[str, dict[str, dict[str, Any]]] = {}
        self._halted = threading.Event()
        self._log: list[str] = []
        self.last_result: Optional[DeskResult] = None

    # -- plumbing -----------------------------------------------------------
    def _say(self, message: str, level: str = "info") -> None:
        line = f"[desk:{level}] {message}"
        self._log.append(line)
        if self.log_cb:
            try:
                self.log_cb(line, level)
            except TypeError:
                self.log_cb(line)
        elif self.status_cb:
            self.status_cb(line)

    def _cancelled(self) -> bool:
        if self._halted.is_set():
            return True
        try:
            return bool(self.cancel_cb and self.cancel_cb())
        except Exception:  # noqa: BLE001
            return False

    def _broker(self, venue: str):
        if venue not in self._brokers:
            broker = make_broker(venue, self.requested_mode, starting_cash=self.starting_cash)
            self._brokers[venue] = broker
            if broker.mode != self.requested_mode:
                self._say(f"{venue}: {self.requested_mode} requested, no credentials — "
                          f"running {broker.mode}")
            if isinstance(broker, PaperBroker):
                self._hydrate(broker, venue)
            elif hasattr(broker, "symbols"):  # a live venue that needs mints
                self._seed_mints(broker)
            self._say(f"{venue}: {broker.name} broker in {broker.mode} mode")
            if broker.mode == "live":
                # Credentials are permission, not balance: a signed key on an
                # unfunded account reads fine and still refuses every buy.
                try:
                    if broker.cash() <= 0:
                        self._say(f"{venue}: live but no buying power — buys will "
                                  "be refused until the account is funded")
                except Exception:  # noqa: BLE001 - a cash read must not abort the run
                    pass
        return self._brokers[venue]

    def _seed_mints(self, broker) -> None:
        """Give a live broker the symbol<->mint map and cost basis it cannot
        read from the chain. Both come from the ledger; a symbol it has never
        seen simply stays unmapped and is skipped by the gate."""
        broker.symbols = dict(self._mints)
        broker.costs = {p.symbol: p.avg_price for p in open_positions(self.conn, broker.mode)}

    def _learn_mint(self, symbol: str, mint: str) -> None:
        if mint and self._mints.get(symbol) != mint:
            self._mints[symbol] = mint
            for broker in self._brokers.values():
                if hasattr(broker, "symbols"):
                    broker.symbols = dict(self._mints)

    def _hydrate(self, broker: PaperBroker, venue: str) -> None:
        """Rebuild a paper book from the ledger so a restart resumes it.

        Without this the desk forgets every holding when the process exits:
        stops never fire across runs, PANIC finds nothing to sell, and cash
        resets to the starting capital. Cash is replayed from the fills; the
        open book (with its stops) is taken from the positions table.
        """
        broker.replay(fills_for(self.conn, broker.mode, venue))
        opening = [p for p in open_positions(self.conn, broker.mode)
                   if (p.venue or venue) == venue]
        if opening:
            broker.adopt(opening)
            self._say(f"{venue}: restored {len(opening)} open position(s) from the ledger")

    def kill_file_present(self) -> bool:
        return DESK_KILL_FILE.is_file()

    # -- the kill switch ----------------------------------------------------
    def panic(self, flatten: bool = True, reason: str = "manual panic") -> str:
        """Trip the switch. Flattens every paper position at the last price."""
        self._halted.set()
        try:
            DESK_KILL_FILE.parent.mkdir(parents=True, exist_ok=True)
            DESK_KILL_FILE.write_text(reason + "\n", encoding="utf-8")
        except OSError:
            pass
        sold = 0
        if flatten:
            for venue in self.venues:
                broker = self._broker(venue)
                for position in list(broker.positions()):
                    price = self._mark(position.symbol, position.avg_price, venue)
                    fill = broker.submit(Order(symbol=position.symbol, side="sell",
                                               qty=position.qty, price=price,
                                               venue=venue, mode=broker.mode,
                                               mint=self._mints.get(position.symbol, "")))
                    if fill:
                        record_fill(self.conn, fill)
                        delete_position(self.conn, position.symbol, broker.mode)
                        sold += 1
        if self.last_result is not None:
            # The poll reads last_result, which still lists what was just sold.
            # Nothing else runs after panic, so refresh it here or the table
            # keeps showing positions the desk no longer holds.
            book = self._live_book()
            if book is not None:
                self.last_result.positions, self.last_result.equity = book
        self._say(f"PANIC — {reason}; flattened {sold} position(s)", "warn")
        return reason

    def resume(self) -> None:
        self._halted.clear()
        try:
            DESK_KILL_FILE.unlink()
        except OSError:
            pass

    # -- marks --------------------------------------------------------------
    def _mark(self, symbol: str, fallback: Optional[float],
              venue: str = "stocks") -> Optional[float]:
        """Latest price, cached for a few seconds.

        The exit loop and the snapshot both mark the same symbol in one cycle,
        and a mark that is 20 seconds stale is worth more than the extra
        request: this is a swing desk, not a scalper.
        """
        from desk_data import source_for

        now = time.monotonic()
        hit = self._mark_cache.get(symbol)
        if hit and now - hit[0] < MARK_CACHE_SECONDS:
            return hit[1]
        try:
            # By venue, not by "-USD" in the name: a DexScreener token like SOL
            # was quoted as a Yahoo *stock* ticker. Crypto is quoted by mint
            # when known, so a same-named token on another chain cannot answer.
            price = source_for(venue).quote(
                (self._mints.get(symbol) or symbol) if venue == "crypto" else symbol)
        except (DataUnavailable, Exception):  # noqa: BLE001
            return fallback
        self._mark_cache[symbol] = (now, price)
        return price

    # -- caching ------------------------------------------------------------
    def _judged(self, cand: Candidate, facts: dict[str, Any],
                candidate_id: Optional[int]) -> Decision:
        """The judge call, cached per symbol for a quarter of an hour.

        The judge is the one model call in a cycle, and on a daily-bar desk a
        repeat question minutes later carries the same bars, the same rule
        verdict and the same dial, so a second opinion costs a request and buys
        nothing. The cached answer is *copied* on the way out: the gate stamps
        ``vetoed_by`` onto whatever object it is handed and that stamp belongs
        to this cycle's row, not to the next one.
        """
        key = cand.symbol
        now = time.monotonic()
        hit = self._judge_cache.get(key)
        if hit and now - hit[0] < JUDGE_CACHE_SECONDS:
            # brain="cache": the row is real, but it is not a fresh judge call,
            # and the scorecard must not count one answer once a minute.
            return replace(hit[1], candidate_id=candidate_id,
                           vetoed_by="", id=None, brain="cache")
        decision = judge(cand, facts, self.brain, candidate_id)
        self._judge_cache[key] = (now, decision)
        return decision

    def _bars(self, cand: Candidate) -> list:
        """Daily bars for a candidate, cached. They only change once a day, and
        refetching two years of them every cycle is what made the desk slow."""
        key = cand.symbol
        now = time.monotonic()
        hit = self._bars_cache.get(key)
        if hit and now - hit[0] < BAR_CACHE_SECONDS:
            return hit[1]
        bars = bars_for(cand)
        self._bars_cache[key] = (now, bars)
        return bars

    # -- daily dial ---------------------------------------------------------
    def _dial(self, venue: str):
        # The dial is a daily number; recomputing it every cycle only re-fetches
        # two years of the benchmark.
        hit = self._dial_cache.get(venue)
        if hit and time.monotonic() - hit[0] < DIAL_CACHE_SECONDS:
            return hit[1]
        dial = self._compute_dial(venue)
        self._dial_cache[venue] = (time.monotonic(), dial)
        return dial

    def _compute_dial(self, venue: str):
        benchmark = "BTC-USD" if venue == "crypto" else "SPY"
        try:
            from desk_data import YahooSource, _days_ago
            # The dial wants the 200-day average, which is ~290 candles. Ask
            # for 420 days rather than a decade: same answer, a fifth of the
            # payload. On a cached miss this is the cycle's heaviest request.
            bars = YahooSource().bars(benchmark, start=_days_ago(DIAL_LOOKBACK_DAYS))
        except DataUnavailable:
            bars = []
        dial = compute_dial(bars)
        try:
            DESK_DIAL_FILE.parent.mkdir(parents=True, exist_ok=True)
            DESK_DIAL_FILE.write_text(json.dumps(
                {"venue": venue, "benchmark": benchmark, "value": dial.value,
                 "reason": dial.reason}), encoding="utf-8")
        except OSError:
            pass
        return dial

    # -- learning from the record -------------------------------------------
    def _record(self, venue: str) -> dict[str, Any]:
        """The desk's own closed trades on this venue, summarised.

        Paper and live ledgers are kept apart because a paper fill proves the
        process, not the venue. Never raises: a summary is an input, not a gate.
        """
        try:
            trades = closed_trades(self.conn, self._broker(venue).mode, venue,
                                   limit=DESK_RECORD_WINDOW)
        except Exception:  # noqa: BLE001 — a read must not break a cycle
            return record_summary([])
        return record_summary(trades)

    def _blocked_rules(self, venue: str) -> dict[str, dict[str, Any]]:
        """Rules this venue has actually traded, and lost with.

        The historical gate benches a rule the years never vindicated. This is
        the other half: a rule the paper log has since disproved. A rule needs
        ``DESK_RECORD_MIN_TRADES`` closed trades below ``DESK_RECORD_WIN_FLOOR``
        before it is stood down, so one early loss does not remove it. Never
        raises — an unreadable ledger blocks nothing.
        """
        try:
            grouped = rule_records(self.conn, self._broker(venue).mode, venue,
                                   limit=DESK_RECORD_WINDOW * 3)
        except Exception:  # noqa: BLE001
            return {}
        blocked: dict[str, dict[str, Any]] = {}
        for rule_id, trades in grouped.items():
            summary = record_summary(trades)
            rate = summary["win_rate"]
            if rate is not None and summary["closed_trades"] >= DESK_RECORD_MIN_TRADES \
                    and rate < DESK_RECORD_WIN_FLOOR:
                blocked[rule_id] = summary
        return blocked

    def _block_note(self, blocked: dict[str, dict[str, Any]]) -> str:
        return ", ".join(
            f"{rule} {s['win_rate']:.0%} over {s['closed_trades']}"
            for rule, s in sorted(blocked.items()))

    # -- the gate -----------------------------------------------------------
    def _walk_forward(self, cand: Candidate, bars: list) -> tuple[bool, str]:
        """Memoised three-verdicts gate. Also writes the verdict rows."""
        from desk_backtest import RULES, evaluate

        if not bars:
            # Fail closed: no history means no rule was validated, and a fetch
            # that failed once must not let a name through untested.
            return False, "no daily history to validate a rule on"
        blocked = self._rule_blocks.get(cand.venue, {})
        # The verdict is a daily fact; a runner that lives for days re-judges.
        key = (cand.symbol, now()[:10], tuple(sorted(blocked)))
        if key not in self._verdict_cache:
            passed, reason, rule_id, params = False, "benched", "", {}
            for rule in RULES.values():
                if rule.rule_id in blocked:
                    reason = "benched"
                    continue
                ok, verdicts = evaluate(cand.symbol, bars, rule, costs=desk_costs(cand.venue))
                for verdict in verdicts:
                    record_verdict(self.conn, verdict)
                if ok:
                    passed, reason = True, f"passed all verdicts ({rule.rule_id})"
                    rule_id = rule.rule_id
                    params = next((v.metrics.get("params") or {} for v in verdicts
                                   if v.verdict == "train"), {})
                    break
                if reason == "benched":
                    reason = verdicts[-1].verdict if verdicts else "benched"
            if not passed:
                if blocked and reason == "benched":
                    reason = f"every rule record-benched ({self._block_note(blocked)})"
                else:
                    reason = f"every rule benched (stopped at '{reason}')"
            self._verdict_cache[key] = (passed, reason, rule_id, params)
        passed, reason, rule_id, params = self._verdict_cache[key]
        if passed:  # stamped on a cache hit too, or the trade carries no rule
            cand.rule_id, cand.params = rule_id, dict(params)
        return passed, reason

    # -- one cycle ----------------------------------------------------------
    def _cycle(self, venue: str, result: DeskResult) -> None:
        broker = self._broker(venue)

        # 1. exits first, so cash and slots free up for this cycle. The stops
        # live in the ledger, not in the broker: a live broker reports what the
        # chain holds and knows nothing about our entry or our limits.
        stops = {p.symbol: (p.stop, p.take, p.atr_pct, p.rule_id, p.params)
                 for p in open_positions(self.conn, broker.mode)}
        for position in list(broker.positions()):
            (position.stop, position.take, position.atr_pct,
             position.rule_id, position.params) = stops.get(
                position.symbol,
                (position.stop, position.take, position.atr_pct,
                 position.rule_id, position.params))
            price = self._mark(position.symbol, None, venue)
            if price is None:
                # Falling back to the entry price made every stop and target
                # look untouched; better to say so and test it next cycle.
                self._say(f"{venue}: no price for {position.symbol} — exit checks skipped", "warn")
                continue
            self._marks[position.symbol] = price
            # Ratchet before testing: a winner that has given back part of its
            # run should die at the trailed stop, not the original one. The
            # trail is measured in this name's own range, as the stop was.
            trailed = trail_stop(position, price, position.atr_pct)
            if trailed > (position.stop or 0.0):
                position.stop = trailed
                upsert_position(self.conn, position)
                held = getattr(broker, "position", lambda _s: None)(position.symbol)
                if held is not None:
                    held.stop = trailed
            why = should_exit(position, price)
            if not why and position.rule_id in RULES:
                # The ATR stop is a safety net; the rule's own exit is the one
                # the backtest was judged on.
                closes = [b.close for b in self._bars(Candidate(symbol=position.symbol,
                                                                venue=venue))]
                if closes and RULES[position.rule_id].exit(closes, position.params):
                    why = f"{position.rule_id} exit signal"
            if not why:
                continue
            fill = broker.submit(Order(symbol=position.symbol, side="sell",
                                       qty=position.qty, price=price, venue=venue,
                                       mode=broker.mode,
                                       mint=self._mints.get(position.symbol, "")))
            if fill:
                record_fill(self.conn, fill)
                delete_position(self.conn, position.symbol, broker.mode)
                result.filled += 1
                self._say(f"{venue}: sold {position.symbol} — {why}")
                notify(f"Aether desk [{broker.mode.upper()}] — SOLD {position.symbol}",
                       f"{position.qty:g} @ {fill.price:g} · {why}")

        # 2. scan
        if self.status_cb:
            self.status_cb(f"desk: scanning {venue}…")
        candidates = scan(venue, self.scan_limit)
        result.scanned += len(candidates)
        self._say(f"{venue}: {len(candidates)} candidate(s)")

        open_now = {p.symbol for p in broker.positions()}
        # The record can only ever cut the dial, never raise it. A cold patch is
        # handled by arithmetic; the market still sets the ceiling.
        record = self._record(venue)
        self._records[venue] = record
        # A rule that has lost its own trades is stood down by name, before the
        # historical gate gets a chance to pass it. Refreshed every cycle, since
        # an exit this cycle can change the record the next one reads.
        self._rule_blocks[venue] = self._blocked_rules(venue)
        if self._rule_blocks[venue]:
            self._say(f"{venue}: rule(s) stood down by record — "
                      f"{self._block_note(self._rule_blocks[venue])}")
        dial = adapt_dial(self._dial(venue), record,
                          min_trades=DESK_RECORD_MIN_TRADES,
                          win_floor=DESK_RECORD_WIN_FLOOR)
        if record["closed_trades"] >= DESK_RECORD_MIN_TRADES:
            self._say(f"{venue}: record {record['win_rate']:.0%} win over "
                      f"{record['closed_trades']} closed; dial {dial.value:.2f}")
        budget = chief_budget(self._equity([venue])[0], broker.cash(), dial, len(open_now))
        held_at_start = len(open_now)

        for cand in sorted(candidates, key=score_candidate, reverse=True):
            if self._cancelled() or self._halted.is_set():
                return

            cand.score = score_candidate(cand)
            candidate_id = record_candidate(self.conn, cand)
            self._learn_mint(cand.symbol, cand.mint)

            gate = gate_candidate(
                cand, bars_count=int(cand.raw.get("bars_count", 0) or 0),
                already_held=cand.symbol in open_now,
                # budget.slots already excludes what was held at the start;
                # only this cycle's buys come off it.
                slots=max(0, budget.slots - (len(open_now) - held_at_start)),
            )
            if not gate.ok:
                self._skip(cand, candidate_id, gate.reason, gate.check, result)
                continue

            bars = self._bars(cand)
            passed, reason = self._walk_forward(cand, bars)
            if not passed:
                self._skip(cand, candidate_id, reason, "walk_forward", result)
                continue
            set_candidate_rule(self.conn, candidate_id, cand.rule_id, cand.params)
            # The gate proved the rule's *signals* make money, so trade only on
            # them: without this the desk bought any day the judge liked a name.
            if not RULES[cand.rule_id].entry([b.close for b in bars], cand.params):
                self._skip(cand, candidate_id, f"no {cand.rule_id} entry signal today",
                           "signal", result)
                continue
            sector = DESK_SECTORS.get(cand.symbol, cand.symbol)
            in_sector = sum(DESK_SECTORS.get(s, s) == sector for s in open_now)
            if in_sector >= DESK_MAX_PER_SECTOR:  # before the judge: no model call spent
                self._skip(cand, candidate_id,
                           f"sector cap: already {in_sector} {sector} position(s)", "sector", result)
                continue
            result.gated += 1

            # One reading of the name's own noise, used by every seat below:
            # the facts the judge sees, the size, the stop and (once held) the
            # trail. Reading it per seat would let the four disagree.
            risk_pct = atr_pct(bars)

            facts = judge_evidence(cand, bars, dial, gate_note=reason,
                                   record=record if record["closed_trades"] else None)
            decision = self._judged(cand, facts, candidate_id)

            verdict = gate_decision(decision)
            if not verdict.ok:
                # Stamp the veto *before* the row is written. Recording first
                # and mutating after leaves a bare skip with nothing explaining
                # it, which defeats the point of logging skips. A note the
                # brain wrote itself beats our paraphrase of it.
                decision.vetoed_by = verdict.check
                decision.skipped_reason = decision.skipped_reason or verdict.reason
            record_decision(self.conn, decision)
            result.judged += 1
            if not verdict.ok:
                self._skip(cand, candidate_id, verdict.reason, verdict.check, result,
                           decision=decision)
                continue

            qty = size_position(budget.per_position, cand.price,
                                decision.confidence, risk_pct)
            fill = broker.submit(Order(symbol=cand.symbol, side="buy", qty=qty,
                                       price=cand.price, venue=venue,
                                       mode=broker.mode, mint=cand.mint))
            if not fill:
                self._skip(cand, candidate_id, "broker refused (cash or size)",
                           "broker", result, decision=decision)
                continue

            fill.candidate_id, fill.decision_id = candidate_id, decision.id
            record_fill(self.conn, fill)
            position = position_from_fill(cand.symbol, fill.qty, fill.price, venue,
                                          broker.mode, risk_pct,
                                          rule_id=cand.rule_id, params=cand.params)
            upsert_position(self.conn, position)
            # Give the broker's own copy the same stop and target, otherwise
            # should_exit() on the next cycle compares against None and the
            # stop never fires.
            held = getattr(broker, "position", lambda _s: None)(cand.symbol)
            if held is not None:
                held.stop, held.take = position.stop, position.take
            set_candidate_status(self.conn, candidate_id, "filled")
            self._note(decision)
            open_now.add(cand.symbol)
            self._recent_fills[cand.symbol] = 1
            result.filled += 1
            self._say(f"{venue}: BOUGHT {qty:g} {cand.symbol} @ {fill.price:g} "
                      f"(conf {decision.confidence:.2f})", "trade")
            notify(f"Aether desk [{broker.mode.upper()}] — BOUGHT {cand.symbol}",
                   f"{qty:g} @ {fill.price:g} · {cand.rule_id} signal · judge {decision.confidence:.0%}")

    def _skip(self, cand: Candidate, candidate_id: int, reason: str, check: str,
              result: DeskResult, decision: Optional[Decision] = None) -> None:
        """Record a skip as a first-class row. This is the point of the ledger.

        The veto is stamped *before* the insert. Recording first and mutating
        the object afterwards leaves a bare row with no ``vetoed_by`` and no
        reason — exactly the information the ledger exists to keep.
        """
        if decision is None:
            decision = Decision(symbol=cand.symbol, action="skip", confidence=0.0,
                                brain="gate", reason=reason)
            decision.candidate_id = candidate_id
        decision.vetoed_by = check
        # A note the brain wrote itself beats our paraphrase of it.
        decision.skipped_reason = decision.skipped_reason or reason
        if decision.id is None:  # the judge path already recorded it
            record_decision(self.conn, decision)
        set_candidate_status(self.conn, candidate_id, f"skipped:{check}")
        self._note(decision)
        result.skips += 1

    def _note(self, decision: Decision) -> None:
        """Keep the recent tail of decisions for the UI. Never raises."""
        try:
            self._recent.append({
                "symbol": decision.symbol,
                "action": decision.action,
                "confidence": round(decision.confidence, 2),
                "vetoed_by": decision.vetoed_by or "",
                "reason": (decision.skipped_reason or decision.reason or "")[:120],
            })
        except Exception:  # noqa: BLE001 — a display aid must not break a cycle
            pass

    # -- the run ------------------------------------------------------------
    def _equity(self, venues: Optional[list[str]] = None) -> tuple[float, float]:
        """``(equity, cash)`` over ``venues`` (default all), positions at marks.

        Computed here rather than by ``broker.equity()``: that valued positions
        at cost (open losses were invisible to the kill switch), and the live
        brokers do not all implement it.
        """
        venues = venues or self.venues
        cash = sum(self._broker(v).cash() for v in venues)
        held = sum(p.qty * self._marks.get(p.symbol, p.avg_price)
                   for v in venues for p in self._broker(v).positions())
        return cash + held, cash

    def run(self, cycles: int = 1) -> DeskResult:
        result = DeskResult(venues=list(self.venues), cycles=cycles,
                            log=self._log, ledger=str(self.conn and ""))
        equity0 = self._equity()[0]
        result.start_equity = result.equity = equity0

        if self.kill_file_present():
            self._halted.set()
            self._say("kill file present — desk halted", "warn")
            result.kill_switch = True
            result.kill_reason = "kill file present"
            result.report = self._report(result)
            self.last_result = result
            return result

        for cycle in range(1, max(1, cycles) + 1):
            if self._cancelled():
                self._say("cancelled")
                break
            self._say(f"— cycle {cycle}/{cycles} —")
            for venue in self.venues:
                if self._cancelled() or self._halted.is_set():
                    break
                try:
                    self._cycle(venue, result)
                except DataUnavailable as exc:
                    self._say(f"{venue}: data unavailable ({exc})", "warn")
                except Exception as exc:  # noqa: BLE001 — one bad venue must not kill the run
                    self._say(f"{venue}: error {type(exc).__name__}: {exc}", "warn")

            equity, cash = self._equity()
            result.equity = equity
            modes = {self._broker(v).mode for v in self.venues}
            mode = modes.pop() if len(modes) == 1 else "mixed"
            day_pnl_pct, drawdown, losing_days = risk_inputs(
                equity_history(self.conn, mode), equity, now()[:10])
            record_equity(self.conn, equity, cash, positions_value=equity - cash,
                          drawdown=drawdown, day_pnl=day_pnl_pct, mode=mode)

            kill = risk_check(day_pnl_pct=day_pnl_pct, drawdown_pct=drawdown,
                              losing_days=losing_days)
            if kill.tripped:
                result.kill_switch, result.kill_reason = True, kill.reason
                self._say(f"KILL SWITCH: {kill.reason}", "warn")
                notify(f"Aether desk [{mode.upper()}] — KILL SWITCH",
                       f"{kill.reason}. Flattening all positions; the desk is halted.")
                self.panic(flatten=True, reason=kill.reason)
                break

        result.positions = [_row(p, self._marks.get(p.symbol))
                            for venue in self.venues
                            for p in self._broker(venue).positions()]
        for venue in self.venues:
            self._broker(venue).starting_cash  # touch, keeps venues materialised
        # The effective dial, not the market dial: the UI shows what actually
        # sized the trades, so a record cut cannot hide behind a healthy number.
        dials = [adapt_dial(self._dial(v), self._records.get(v, {}),
                            min_trades=DESK_RECORD_MIN_TRADES,
                            win_floor=DESK_RECORD_WIN_FLOOR).value
                 for v in self.venues]
        result.dial = round(sum(dials) / len(dials), 3) if dials else 0.0
        result.ledger = str(self.conn.execute("PRAGMA database_list").fetchone()[2])
        record_run(self.conn, self.requested_mode, result.scanned, result.gated,
                   result.filled, result.kill_switch, result.kill_reason)
        result.equity = self._equity()[0]
        result.report = self._report(result)
        self.last_result = result
        self._write_log(result)
        return result

    # -- reporting ----------------------------------------------------------
    def _report(self, result: DeskResult) -> str:
        lines = [
            f"Aether desk — {result.cycles} cycle(s), mode {self.requested_mode}",
            f"venues: {', '.join(result.venues)}",
            f"scanned {result.scanned}, cleared gate {result.gated}, "
            f"judged {result.judged}, filled {result.filled}, skipped {result.skips}",
            f"equity {result.equity:,.2f} (start {result.start_equity:,.2f}, "
            f"P&L {result.pnl:+,.2f}"
            + (f" / {result.pnl / result.start_equity * 100:+.2f}%"
               if result.start_equity else "") + ")",
            f"daily dial {result.dial:.3f}",
        ]
        if result.positions:
            lines.append("open positions:")
            lines += [f"  {p['symbol']} qty {_num(p['qty']):g} "
                      f"@ {_num(p['avg_price']):g} stop {_num(p['stop']):g} "
                      f"take {_num(p['take']):g}"
                      + (f"  pnl {p['pnl']:+,.2f} ({p['pnl_pct']:+.2f}%)"
                         if p.get("pnl") is not None else "")
                      for p in result.positions]
        else:
            lines.append("open positions: none")
        closed = {v: r for v, r in self._records.items() if r.get("closed_trades")}
        if closed:
            lines.append("closed record:")
            for venue, r in closed.items():
                lines.append(
                    f"  {venue}: {r['closed_trades']} closed, "
                    f"{r['win_rate']:.0%} win, expectancy {r['expectancy_pct']:+.2f}%, "
                    f"last {', '.join(r['last_5']) or 'none'}")
        if result.kill_switch:
            lines.append(f"KILL SWITCH: {result.kill_reason}")
        return "\n".join(lines)

    def _write_log(self, result: DeskResult) -> None:
        try:
            DESK_LOGS.mkdir(parents=True, exist_ok=True)
            from desk_ledger import now
            stamp = now().replace(":", "-")
            path = DESK_LOGS / f"{stamp}.log"
            path.write_text("\n".join(self._log + ["", result.report]), encoding="utf-8")
        except OSError:
            pass

    def _live_book(self) -> Optional[tuple[list[dict], float]]:
        """Open positions and equity straight from the paper brokers.

        Returns ``None`` when any venue is a live broker, whose reads hit the
        network and would stall the poll; the caller then uses the last result.
        """
        rows: list[dict] = []
        equity = 0.0
        for venue in self.venues:
            broker = self._broker(venue)
            if not isinstance(broker, PaperBroker):
                return None
            equity += self._equity([venue])[0]
            rows += [_row(p, self._marks.get(p.symbol))
                     for p in broker.positions()]
        return rows, equity

    def state(self) -> dict[str, Any]:
        """Snapshot for the UI to poll. Never blocks on the network."""
        result = self.last_result
        book = None if result else self._live_book()
        equity = result.equity if result else (book[1] if book else None)
        start = result.start_equity if result else self.starting_cash
        return {
            "pnl": round(equity - start, 4) if equity is not None and start else None,
            "running": not self._halted.is_set(),
            "halted": self._halted.is_set(),
            "venues": self.venues,
            "mode": self.requested_mode,
            "equity": result.equity if result else (book[1] if book else None),
            "positions": result.positions if result else (book[0] if book else []),
            "dial": result.dial if result else 0.0,
            "skips": result.skips if result else 0,
            "filled": result.filled if result else 0,
            "report": result.report if result else "",
            "recent": list(reversed(self._recent)),
            "record": {v: r for v, r in self._records.items()
                       if r.get("closed_trades")},
            "health": self._health(),
        }

    def _health(self) -> list[str]:
        """Reasons the desk is not doing what it looks like it is doing.

        Everything here is a guess a viewer would otherwise have to make and
        would usually make wrong — a live badge on an unsignable venue, a run
        that quietly fills nothing.
        """
        notes: list[str] = []
        if self.requested_mode == "live":
            for venue, broker in self._brokers.items():
                if broker.mode != "live":
                    notes.append(f"{venue}: live requested, running {broker.mode}")
                elif not self._marks:
                    notes.append(f"{venue}: live broker has no marks yet")
            if self.brain is not None and getattr(self.brain, "name", "") == "stub":
                notes.append("stub brain — every candidate gets the same answer")
        if self._halted.is_set():
            notes.append("halted")
        for venue, record in self._records.items():
            rate = record.get("win_rate")
            if (rate is not None and record["closed_trades"] >= DESK_RECORD_MIN_TRADES
                    and rate < DESK_RECORD_WIN_FLOOR):
                notes.append(f"{venue}: {rate:.0%} win over {record['closed_trades']} "
                             f"closed — size cut")
        return notes
