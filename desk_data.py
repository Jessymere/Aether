"""Market data in. Read-only; this module never places anything.

Two live, key-less sources are used, both verified working on this machine:

  * Yahoo chart  -- daily OHLCV for equities, ETFs and crypto pairs.
  * DexScreener  -- Solana/SOL pairs with price, liquidity and 24h volume.

GMGN's unofficial ``defi/quotation`` endpoint answers a plain request with a
Cloudflare page (HTTP 403), so it is treated as strictly best-effort: it either
contributes or it silently defers to DexScreener. Nothing depends on it.

Fetching and parsing are deliberately separate — ``fetch_*`` touches the
network, ``parse_*`` is pure — so tests can feed canned JSON with no sockets.

Responses are cached under ``config.DESK_CACHE`` for the calendar day. The
cache is the source of truth for a run, so a flaky upstream cannot make two
cycles in the same day disagree, and re-running is free.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import DESK_CACHE, DESK_LIQUIDITY_FLOOR, GMGN_API_KEY

#: A browser-ish agent. Yahoo rejects the default urllib agent outright.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AetherDesk/1.0"
DEFAULT_TIMEOUT = 20


class DataUnavailable(RuntimeError):
    """Raised when a source cannot answer. Callers fall back, never crash."""


@dataclass
class Bar:
    """One daily candle. ``ts`` is a UTC date string."""

    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    symbol: str = ""


@dataclass
class Candidate:
    """A tradable idea found by the SCAN seat. Carries no opinion."""

    symbol: str
    venue: str = "stocks"  # "stocks" | "crypto"
    source: str = ""
    price: float = 0.0
    liquidity: float = 0.0
    fdv: float = 0.0
    vol_24h: float = 0.0
    change_24h: float = 0.0
    rule_id: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    status: str = "scanned"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def mint(self) -> str:
        """The Solana mint, when the source reported one.

        DexScreener can return pairs from any chain for the same query, so the
        chain is checked here rather than trusted: a mint that belongs to Base
        would make Jupiter quote nonsense.
        """
        if str(self.raw.get("chainId", "")).lower() != "solana":
            return ""
        return str(self.raw.get("baseAddress") or "")


# ---------------------------------------------------------------------------
# Fetch with a same-day disk cache
# ---------------------------------------------------------------------------
def _cache_path(url: str, bucket: str) -> Path:
    digest = hashlib.sha256(f"{bucket}:{url}".encode()).hexdigest()[:20]
    return DESK_CACHE / f"{digest}.json"


def _cache_read(url: str, bucket: str) -> Optional[Any]:
    path = _cache_path(url, bucket)
    if not path.is_file():
        return None
    try:
        if path.stat().st_mtime < time.time() - 6 * 3600:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _cache_write(url: str, bucket: str, payload: Any) -> None:
    try:
        DESK_CACHE.mkdir(parents=True, exist_ok=True)
        _cache_path(url, bucket).write_text(
            json.dumps(payload), encoding="utf-8"
        )
    except OSError:
        pass  # a cache miss is never fatal


def _decode(response, raw: bytes) -> str:
    """Decompress a response body, then decode it.

    urllib asks for nothing by default, so a compressed body would otherwise be
    fed to ``json.loads`` as mojibake. Both encodings are accepted because
    Yahoo serves gzip and some proxies answer deflate; a header we do not know
    is passed through untouched rather than guessed at.
    """
    encoding = (response.headers.get("Content-Encoding") or "").strip().lower()
    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)  # raw deflate
    return raw.decode("utf-8", errors="replace")


def fetch_json(url: str, *, bucket: str = "generic", timeout: int = DEFAULT_TIMEOUT,
               use_cache: bool = True, headers: Optional[dict] = None) -> Any:
    """GET a URL and return decoded JSON, caching by (bucket, url).

    Raises DataUnavailable on any failure — including an HTML body where JSON
    was expected, which is how Cloudflare presents itself.
    """
    if use_cache:
        cached = _cache_read(url, bucket)
        if cached is not None:
            return cached

    # gzip is asked for explicitly and decoded by hand: a two-year Yahoo chart
    # is a few hundred KB of JSON, which matters on the dial's hot path.
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate",
        **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = _decode(response, response.read())
    except urllib.error.HTTPError as exc:
        raise DataUnavailable(f"HTTP {exc.code} from {url}") from exc
    except urllib.error.URLError as exc:
        raise DataUnavailable(f"Could not reach {url}: {exc}") from exc
    except (gzip.BadGzipFile, zlib.error, EOFError) as exc:
        raise DataUnavailable(f"Undecodable body from {url}: {exc}") from exc
    except OSError as exc:
        raise DataUnavailable(f"Network error for {url}: {exc}") from exc

    try:
        payload = json.loads(body)
    except ValueError as exc:
        snippet = body.strip()[:80].replace("\n", " ")
        raise DataUnavailable(f"Non-JSON response from {url} ({snippet!r})") from exc

    if use_cache:
        _cache_write(url, bucket, payload)
    return payload


# ---------------------------------------------------------------------------
# Parsers (pure — no network, so tests can drive these directly)
# ---------------------------------------------------------------------------
def parse_yahoo_chart(payload: dict, symbol: str = "") -> list[Bar]:
    """Turn a Yahoo ``v8/finance/chart`` body into bars, dropping null closes."""
    result = ((payload or {}).get("chart") or {}).get("result") or []
    if not result:
        return []
    block = result[0]
    stamps = block.get("timestamp") or []
    quote = ((block.get("indicators") or {}).get("quote") or [{}])[0]

    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    bars: list[Bar] = []
    for i, stamp in enumerate(stamps):
        def at(seq: list) -> Optional[float]:
            return seq[i] if i < len(seq) and seq[i] is not None else None

        close = at(closes)
        if close is None:
            continue
        bars.append(Bar(
            ts=datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d"),
            open=at(opens) or close,
            high=at(highs) or close,
            low=at(lows) or close,
            close=close,
            volume=at(volumes) or 0.0,
            symbol=symbol,
        ))
    return bars


def parse_dexscreener_pairs(payload: dict, venue: str = "crypto",
                            min_liquidity: float = 0.0) -> list[Candidate]:
    """Turn a DexScreener body into candidates, best pair per token.

    DexScreener returns one entry per *pair*, so a token with several pools
    would otherwise appear repeatedly. The deepest pool wins.
    """
    best: dict[str, Candidate] = {}
    for pair in (payload or {}).get("pairs") or []:
        base = pair.get("baseToken") or {}
        symbol = (base.get("symbol") or "").strip()
        if not symbol:
            continue
        try:
            liquidity = float((pair.get("liquidity") or {}).get("usd") or 0.0)
            price = float(pair.get("priceUsd") or 0.0)
        except (TypeError, ValueError):
            continue
        if price <= 0 or liquidity < min_liquidity:
            continue

        def num(key: str) -> float:
            try:
                return float(((pair.get("volume") or {}).get(key)) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        try:
            change = float((pair.get("priceChange") or {}).get("h24") or 0.0)
        except (TypeError, ValueError):
            change = 0.0

        cand = Candidate(
            symbol=symbol.upper(),
            venue=venue,
            source="dexscreener",
            price=price,
            liquidity=liquidity,
            fdv=float(pair.get("fdv") or 0.0),
            vol_24h=num("h24"),
            change_24h=change,
            raw={
                "chainId": pair.get("chainId", ""),
                "dexId": pair.get("dexId", ""),
                "pairAddress": pair.get("pairAddress", ""),
                "url": pair.get("url", ""),
                "baseAddress": base.get("address", ""),
            },
        )
        existing = best.get(cand.symbol)
        if existing is None or cand.liquidity > existing.liquidity:
            best[cand.symbol] = cand

    # Rank by traded volume, which is a better attention proxy than raw liquidity.
    return sorted(best.values(), key=lambda c: c.vol_24h, reverse=True)


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
class YahooSource:
    """Daily bars for equities, ETFs and crypto pairs. Free, no key."""

    name = "yahoo"
    venue = "stocks"

    BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"

    def bars(self, symbol: str, start: str = "2015-01-01",
             end: Optional[str] = None, use_cache: bool = True) -> list[Bar]:
        end = end or date.today().isoformat()
        query = urllib.parse.urlencode({
            "period1": int(_epoch(start)),
            "period2": int(_epoch(end)),
            "interval": "1d",
            "events": "div,splits",
        })
        payload = fetch_json(
            f"{self.BASE}{urllib.parse.quote(symbol)}?{query}",
            bucket=f"yahoo:{date.today().isoformat()}",
            use_cache=use_cache,
        )
        return parse_yahoo_chart(payload, symbol)

    def quote(self, symbol: str) -> float:
        """Latest close, for marking positions."""
        bars = self.bars(symbol, start=_days_ago(10))
        if not bars:
            raise DataUnavailable(f"No bars for {symbol}")
        return bars[-1].close


class DexScreenerSource:
    """Solana/SOL pairs. Free, no key, returns live liquidity and volume."""

    name = "dexscreener"
    venue = "crypto"

    SEARCH = "https://api.dexscreener.com/latest/dex/search?q="
    TOKENS = "https://api.dexscreener.com/latest/dex/tokens/"

    def search(self, query: str, limit: int = 20,
               min_liquidity: float = 0.0) -> list[Candidate]:
        payload = fetch_json(
            self.SEARCH + urllib.parse.quote(query),
            bucket=f"dexscreener:{date.today().isoformat()}",
        )
        return parse_dexscreener_pairs(payload, min_liquidity=min_liquidity)[:limit]

    def trending(self, limit: int = 20,
                 min_liquidity: float = 0.0) -> list[Candidate]:
        """DexScreener's search relevance feed, filtered by liquidity."""
        return self.search("SOL", limit=max(limit * 3, 30),
                           min_liquidity=min_liquidity)[:limit]

    def quote(self, symbol_or_address: str) -> float:
        payload = fetch_json(
            self.SEARCH + urllib.parse.quote(symbol_or_address),
            bucket=f"dexscreener:{date.today().isoformat()}",
        )
        pairs = parse_dexscreener_pairs(payload)
        if not pairs:
            raise DataUnavailable(f"No DexScreener pair for {symbol_or_address}")
        return pairs[0].price


class GmgnSource:
    """Best-effort GMGN ranks. Falls back to DexScreener without complaint.

    GMGN answers a bare urllib request with a Cloudflare challenge, so this
    class exists to *try* — never to be depended on.
    """

    name = "gmgn"
    venue = "crypto"

    RANK = "https://gmgn.ai/defi/quotation/v1/rank/sol/swaps/1h"

    def __init__(self) -> None:
        self.fallback = DexScreenerSource()

    def trending(self, limit: int = 20, min_liquidity: float = 0.0) -> list[Candidate]:
        query = urllib.parse.urlencode({
            "orderby": "swaps", "direction": "desc", "limit": limit,
        })
        # The key does not reliably clear the challenge from every network, so
        # it rides along and the DexScreener fallback stays in place either way.
        headers = {"x-api-key": GMGN_API_KEY} if GMGN_API_KEY else {}
        try:
            payload = fetch_json(f"{self.RANK}?{query}",
                                 bucket=f"gmgn:{date.today().isoformat()}",
                                 headers=headers)
            data = (payload or {}).get("data") or {}
            ranks = data.get("rank") if isinstance(data, dict) else None
            if not ranks:
                raise DataUnavailable("GMGN returned no rank rows")
        except DataUnavailable:
            return self.fallback.trending(limit=limit, min_liquidity=min_liquidity)

        out: list[Candidate] = []
        for row in ranks:
            try:
                liquidity = float(row.get("liquidity") or 0.0)
                price = float(row.get("price") or 0.0)
            except (TypeError, ValueError):
                continue
            if price <= 0 or liquidity < min_liquidity:
                continue
            out.append(Candidate(
                symbol=(row.get("symbol") or "").upper(),
                venue="crypto",
                source="gmgn",
                price=price,
                liquidity=liquidity,
                fdv=float(row.get("fdv") or 0.0),
                vol_24h=float(row.get("volume_24h") or 0.0),
                change_24h=float(row.get("price_change_percent1h") or 0.0),
                raw={"address": row.get("address", ""), "swaps": row.get("swaps", 0)},
            ))
        return out[:limit] or self.fallback.trending(limit=limit, min_liquidity=min_liquidity)


def source_for(venue: str):
    """Pick a source by venue name."""
    if venue == "crypto":
        return DexScreenerSource()
    return YahooSource()


def default_liquidity_floor() -> float:
    return DESK_LIQUIDITY_FLOOR


# ---------------------------------------------------------------------------
def _epoch(day: str) -> float:
    return datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


def _days_ago(n: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")
