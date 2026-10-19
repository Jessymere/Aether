"""Market data parsers and the fetch/cache layer. No sockets."""

from __future__ import annotations

import json

import pytest

import desk_data as D
from desk_data import Candidate, DataUnavailable, fetch_json, parse_dexscreener_pairs, parse_yahoo_chart


# ---------------------------------------------------------------------------
# Yahoo parser
# ---------------------------------------------------------------------------
def yahoo_payload():
    return {"chart": {"result": [{
        "timestamp": [1_600_000_000, 1_600_086_400, 1_600_172_800],
        "indicators": {"quote": [{
            "open": [100.0, 101.0, None],
            "high": [101.0, 102.0, None],
            "low": [99.0, 100.0, None],
            "close": [100.5, 101.5, None],
            "volume": [1000, 1100, None],
        }]},
    }]}}


def test_yahoo_parser_makes_bars_and_drops_null_closes():
    bars = parse_yahoo_chart(yahoo_payload(), "SPY")
    assert len(bars) == 2  # the third row has a null close
    assert bars[0].close == 100.5 and bars[0].symbol == "SPY"
    assert bars[0].ts.startswith("20")


def test_yahoo_parser_falls_back_to_close_for_a_missing_open():
    payload = yahoo_payload()
    payload["chart"]["result"][0]["indicators"]["quote"][0]["open"][0] = None
    assert parse_yahoo_chart(payload)[0].open == 100.5


def test_yahoo_parser_tolerates_empty_and_junk():
    assert parse_yahoo_chart({}) == []
    assert parse_yahoo_chart({"chart": {"result": []}}) == []
    assert parse_yahoo_chart({"chart": {}}) == []
    assert parse_yahoo_chart(None) == []


# ---------------------------------------------------------------------------
# DexScreener parser
# ---------------------------------------------------------------------------
def pair(symbol, liquidity, vol, price=1.0, change=5.0, address="addr"):
    return {
        "chainId": "solana", "dexId": "raydium", "pairAddress": address,
        "baseToken": {"symbol": symbol, "address": address},
        "priceUsd": str(price), "fdv": 1_000_000,
        "liquidity": {"usd": liquidity},
        "volume": {"h24": vol},
        "priceChange": {"h24": change},
        "url": "https://dexscreener.com/x",
    }


def test_dexscreener_keeps_only_the_deepest_pool_per_token():
    payload = {"pairs": [pair("MEME", 10_000, 500), pair("MEME", 900_000, 100)]}
    out = parse_dexscreener_pairs(payload)
    assert len(out) == 1 and out[0].liquidity == 900_000


def test_dexscreener_ranks_by_traded_volume():
    payload = {"pairs": [pair("A", 100_000, 10), pair("B", 100_000, 9_000)]}
    assert [c.symbol for c in parse_dexscreener_pairs(payload)] == ["B", "A"]


def test_dexscreener_skips_junk_rows_and_zero_prices():
    payload = {"pairs": [
        {}, pair("GOOD", 100_000, 5), pair("ZERO", 100_000, 5, price=0),
        {"baseToken": {"symbol": "NOLIQ"}},
    ]}
    assert [c.symbol for c in parse_dexscreener_pairs(payload)] == ["GOOD"]


def test_dexscreener_applies_the_liquidity_floor():
    payload = {"pairs": [pair("THIN", 1_000, 5), pair("FAT", 500_000, 5)]}
    assert [c.symbol for c in parse_dexscreener_pairs(payload, min_liquidity=50_000)] == ["FAT"]


def test_dexscreener_uppercases_and_keeps_provenance():
    out = parse_dexscreener_pairs({"pairs": [pair("meme", 100_000, 5)]})
    assert out[0].symbol == "MEME"
    assert out[0].venue == "crypto" and out[0].source == "dexscreener"
    assert out[0].raw["chainId"] == "solana" and out[0].raw["pairAddress"] == "addr"


def test_dexscreener_handles_no_pairs():
    assert parse_dexscreener_pairs({}) == []
    assert parse_dexscreener_pairs({"pairs": None}) == []


# ---------------------------------------------------------------------------
# fetch_json
# ---------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, body: bytes, headers: dict | None = None):
        self.body = body
        self.headers = headers or {}

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_urlopen(monkeypatch, tmp_path):
    monkeypatch.setattr(D, "DESK_CACHE", tmp_path / "cache")
    calls = {"n": 0, "body": json.dumps({"ok": True}).encode(), "headers": {}}

    def opener(request, timeout=None):
        calls["n"] += 1
        return FakeResponse(calls["body"], calls["headers"])

    monkeypatch.setattr(D.urllib.request, "urlopen", opener)
    return calls


def test_fetch_json_returns_decoded_payload(fake_urlopen):
    assert fetch_json("https://x.test/a") == {"ok": True}


def test_fetch_json_caches_so_a_second_call_is_free(fake_urlopen):
    fetch_json("https://x.test/a")
    fetch_json("https://x.test/a")
    assert fake_urlopen["n"] == 1


def test_cache_can_be_bypassed(fake_urlopen):
    fetch_json("https://x.test/a")
    fetch_json("https://x.test/a", use_cache=False)
    assert fake_urlopen["n"] == 2


def test_distinct_urls_do_not_collide(fake_urlopen):
    fetch_json("https://x.test/a")
    fetch_json("https://x.test/b")
    assert fake_urlopen["n"] == 2


def test_an_html_body_is_an_error_not_a_payload(fake_urlopen):
    """This is how Cloudflare presents itself. Must not silently pass."""
    fake_urlopen["body"] = b"<html>Attention Required!</html>"
    with pytest.raises(DataUnavailable) as exc:
        fetch_json("https://x.test/cloudflare")
    assert "Non-JSON" in str(exc.value)


def test_http_error_becomes_dataunavailable(fake_urlopen, monkeypatch):
    import urllib.error

    def opener(request, timeout=None):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)

    monkeypatch.setattr(D.urllib.request, "urlopen", opener)
    with pytest.raises(DataUnavailable) as exc:
        fetch_json("https://x.test/forbidden")
    assert "403" in str(exc.value)


def test_network_error_becomes_dataunavailable(fake_urlopen, monkeypatch):
    import urllib.error

    def opener(request, timeout=None):
        raise urllib.error.URLError("dns")

    monkeypatch.setattr(D.urllib.request, "urlopen", opener)
    with pytest.raises(DataUnavailable):
        fetch_json("https://x.test/down")


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
def test_source_for_picks_by_venue():
    assert isinstance(D.source_for("crypto"), D.DexScreenerSource)
    assert isinstance(D.source_for("stocks"), D.YahooSource)


def test_gmgn_falls_back_to_dexscreener_when_blocked(monkeypatch):
    monkeypatch.setattr(D, "fetch_json", lambda *a, **k: (_ for _ in ()).throw(
        DataUnavailable("cloudflare")))
    monkeypatch.setattr(D.DexScreenerSource, "trending",
                        lambda self, limit=10, min_liquidity=0.0: [
                            Candidate(symbol="FALLBACK", venue="crypto")])
    assert D.GmgnSource().trending()[0].symbol == "FALLBACK"


def test_gmgn_parses_rank_rows_when_they_arrive(monkeypatch):
    monkeypatch.setattr(D, "fetch_json", lambda *a, **k: {
        "data": {"rank": [{"symbol": "gm", "price": "2.5", "liquidity": 100_000,
                           "volume_24h": 9, "address": "a"}]}})
    out = D.GmgnSource().trending()
    assert out[0].symbol == "GM" and out[0].source == "gmgn"


def test_gmgn_sends_the_key_when_one_is_configured(monkeypatch):
    seen: dict = {}

    def capture(url, **kwargs):
        seen.update(kwargs.get("headers") or {})
        return {"data": {"rank": []}}

    monkeypatch.setattr(D, "GMGN_API_KEY", "demo-key")
    monkeypatch.setattr(D, "fetch_json", capture)
    monkeypatch.setattr(D.DexScreenerSource, "trending",
                        lambda self, limit=10, min_liquidity=0.0: [])
    D.GmgnSource().trending()
    assert seen == {"x-api-key": "demo-key"}


def test_gmgn_sends_no_key_header_without_one(monkeypatch):
    seen: dict = {}

    def capture(url, **kwargs):
        seen.update(kwargs.get("headers") or {})
        return {"data": {"rank": []}}

    monkeypatch.setattr(D, "GMGN_API_KEY", "")
    monkeypatch.setattr(D, "fetch_json", capture)
    monkeypatch.setattr(D.DexScreenerSource, "trending",
                        lambda self, limit=10, min_liquidity=0.0: [])
    D.GmgnSource().trending()
    assert seen == {}


def test_user_agent_is_not_urllib_default():
    """Yahoo rejects the default agent outright."""
    assert "Mozilla" in D.USER_AGENT


# ---------------------------------------------------------------------------
# Compression — Yahoo answers gzip, and urllib never decodes it for us
# ---------------------------------------------------------------------------
def test_a_gzip_body_is_decoded(fake_urlopen):
    import gzip

    fake_urlopen["body"] = gzip.compress(json.dumps({"ok": "gz"}).encode())
    fake_urlopen["headers"] = {"Content-Encoding": "gzip"}
    assert fetch_json("https://x.test/gz", use_cache=False) == {"ok": "gz"}


def test_a_deflate_body_is_decoded(fake_urlopen):
    import zlib

    fake_urlopen["body"] = zlib.compress(json.dumps({"ok": "df"}).encode())
    fake_urlopen["headers"] = {"Content-Encoding": "deflate"}
    assert fetch_json("https://x.test/df", use_cache=False) == {"ok": "df"}


def test_an_unlabelled_body_is_read_as_plain(fake_urlopen):
    fake_urlopen["headers"] = {}
    assert fetch_json("https://x.test/plain", use_cache=False) == {"ok": True}


def test_a_truncated_gzip_body_is_a_dataunavailable(fake_urlopen):
    import gzip

    fake_urlopen["body"] = gzip.compress(b'{"ok": true}')[:-6]
    fake_urlopen["headers"] = {"Content-Encoding": "gzip"}
    with pytest.raises(DataUnavailable):
        fetch_json("https://x.test/broken", use_cache=False)


def test_the_request_asks_for_compression(fake_urlopen, monkeypatch):
    seen = {}
    real = D.urllib.request.urlopen

    def opener(request, timeout=None):
        seen.update(request.headers)
        return real(request, timeout=timeout)

    monkeypatch.setattr(D.urllib.request, "urlopen", opener)
    fetch_json("https://x.test/hdr", use_cache=False)
    assert seen.get("Accept-encoding") == "gzip, deflate"
