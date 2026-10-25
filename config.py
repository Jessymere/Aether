import os
import sys
from pathlib import Path

OLLAMA_URL = "http://127.0.0.1:11434"
APP_NAME = "Aether"
#: Every default seat uses OpenRouter's free-model router. It selects an
#: available model per request and filters for capabilities such as vision.
MODELS = {
    "planner": "openrouter/free",
    "coder": "openrouter/free",
    "reviewer": "openrouter/free",
    "tester": "openrouter/free",
    "security": "openrouter/free",
    "release": "openrouter/free",
    "decider": "openrouter/free",
    "deepseek_coder": "openrouter/free",
    "deepseek_coder_1017": "openrouter/free",
    "qwen_coder": "openrouter/free",
    "vision": "openrouter/free",
    "link": "openrouter/free",
}
#: ``--fast`` stays on OpenRouter and uses the responsive MiniMax free route.
FAST_MODELS = {
    "planner": "minimax/minimax-m3.1-flash:free",
    "coder": "minimax/minimax-m3.1-flash:free",
    "reviewer": "minimax/minimax-m3.1-flash:free",
    "tester": "minimax/minimax-m3.1-flash:free",
    "security": "minimax/minimax-m3.1-flash:free",
    "release": "minimax/minimax-m3.1-flash:free",
    "deepseek_coder": "minimax/minimax-m3.1-flash:free",
    "deepseek_coder_1017": "minimax/minimax-m3.1-flash:free",
    "qwen_coder": "minimax/minimax-m3.1-flash:free",
    "decider": "minimax/minimax-m3.1-flash:free",
    "vision": "minimax/minimax-m3.1-flash:free",
    "link": "minimax/minimax-m3.1-flash:free",
}
ROOT = Path(__file__).parent


#: Keys the file overrides the environment for. A stale exported key is
#: indistinguishable from a fresh one, and the file is the thing the user
#: actually edits — so for provider credentials the file is authoritative.
_DOTENV_PREFERS_FILE = frozenset({
    "NVIDIA_API_KEY", "OPENROUTER_API_KEY", "EXPLABS_API_KEY", "EXPERIENTIAL_API_KEY",
    "ROBINHOOD_API_KEY", "ROBINHOOD_PRIVATE_KEY",
})


def _load_dotenv(path: Path, prefer_file: frozenset[str] = frozenset()) -> None:
    """Load KEY=VALUE lines from a .env file into os.environ.

    Uses setdefault so a real environment variable wins, except for the keys in
    ``prefer_file``. Stdlib only — no python-dotenv dependency.
    """
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip('"').strip("'")
        if key in prefer_file:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


def _dotenv_path() -> Path:
    """Find app credentials beside a frozen executable or in its source root."""
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        candidates = (executable_dir / ".env", executable_dir.parent.parent / ".env")
        return next((path for path in candidates if path.is_file()), candidates[0])
    return ROOT / ".env"


_load_dotenv(_dotenv_path(), _DOTENV_PREFERS_FILE)

MEMORY_FILE = ROOT / "memory.md"
MEMORY_DB = ROOT / "memory.db"
WORKSPACE = ROOT / "workspace"
RUNS = ROOT / "runs"
MAX_CONTEXT_CHARS = 12_000
MAX_RAW_CONTEXT_CHARS = 60_000

VERSION = "1.0.0"
UPDATE_MANIFEST_URL = "https://your-server.com/aether/manifest.json"

# Experiential Labs gateway
EXPLABS_URL = "https://api.experientiallabs.ai"
# One gateway, one key. EXPLABS_API_KEY is the canonical name; the older
# EXPERIENTIAL_API_KEY is still honoured so existing setups keep working.
GATEWAY_API_KEY = (
    os.environ.get("EXPLABS_API_KEY", "").strip()
    or os.environ.get("EXPERIENTIAL_API_KEY", "").strip()
)
EXPLABS_API_KEY = GATEWAY_API_KEY
EXPLABS_MODELS = {
    "qwen_coder": "qwen3.8-27b",
    "deepseek_coder": "deepseek-v4.1-flash-uncensored",
    "deepseek_coder_1017": "deepseek-v4.1-flash-1017",
    "decider": "gpt-6-luna-decisions",
}
EXPLABS_CHAT_PATH = "/v1/chat/completions"

# Experiential Labs Decisions gateway
EXPERIENTIAL_API_KEY = GATEWAY_API_KEY  # legacy alias for GATEWAY_API_KEY
DECISIONS_URL = "https://api.experientiallabs.ai"
DECISIONS_PATH = "/v1/decisions"
DECISION_MODEL = "gpt-6-luna-decisions"

#: Every model slug that lives on the gateway rather than in local Ollama.
GATEWAY_MODEL_SLUGS = frozenset(EXPLABS_MODELS.values()) | {DECISION_MODEL}

# ---------------------------------------------------------------------------
# Free cloud providers
#
# Both speak the OpenAI wire format, so the only per-provider facts are a base
# URL and a key. Base URLs carry no path: the client appends the endpoint.
# A slug belongs to exactly one provider and a slug in no table is served by
# local Ollama — the ownership map is explicit rather than guessed at from the
# shape of the slug, which is what keeps routing a lookup and not a heuristic.
# ---------------------------------------------------------------------------
NIM_URL = "https://integrate.api.nvidia.com"
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "").strip()
OPENROUTER_URL = "https://openrouter.ai/api"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()

#: provider name -> (base URL, key). The key may be "" — the route still names
#: the provider so the error says which key is missing.
PROVIDERS: dict[str, tuple[str, str]] = {
    "nim": (NIM_URL, NVIDIA_API_KEY),
    "openrouter": (OPENROUTER_URL, OPENROUTER_API_KEY),
}

#: slug -> provider. Anything absent here is local.
MODEL_PROVIDERS: dict[str, str] = {
    "z-ai/glm-5.3": "nim",
    # glm-5.3-flash stalls on NIM (a 400-token reply exceeded 90s repeatedly),
    # so the fast tier uses gpt-oss-20b / lightning instead.
    "openai/gpt-oss-20b": "nim",
    "nvidia/nemotron-3-super-120b-a12b": "nim",
    "nvidia/nemotron-3-ultra-550b-a55b": "nim",
    "nvidia/nemotron-3.5-lightning-30b-a3b": "nim",
    "nvidia/nemotron-3.5-super-vl-120b-a12b": "nim",
    # In the NIM catalogue but not granted to this account (404 when called).
    # "nvidia/nemotron-nano-3-30b-a3b": "nim",
    # "nvidia/nemotron-3.5-nano-vl-8b-a4b": "nim",
    "google/gemma-4-31b-it": "nim",
    "meta/llama-3.2-90b-vision-instruct": "nim",
    "meta/llama-3.2-11b-vision-instruct": "nim",
    # OpenRouter's free tier. ``thinkingmachines/*:free`` is deliberately absent:
    # those endpoints are gated to "agentic harnesses" and answer 403 to a plain
    # API call regardless of headers.
    "nvidia/nemotron-3-ultra-550b-a55b:free": "openrouter",
    "nvidia/nemotron-3-super-120b-a12b:free": "openrouter",
    "cohere/north-mini-code:free": "openrouter",
    "liquid/lfm-2.5-2.6b:free": "openrouter",
    "google/gemma-4-31b-it:free": "openrouter",
    "minimax/minimax-m3.1-flash:free": "openrouter",
    "nvidia/nemotron-3.5-super-vl:free": "openrouter",
    "openrouter/free": "openrouter",
}


def provider_for(model: str) -> str:
    """Return the provider name for a slug, or "" when it is a local model."""
    if model in GATEWAY_MODEL_SLUGS:
        return "gateway"
    return MODEL_PROVIDERS.get(model, "")


def provider_key(provider: str) -> str:
    """The key for a provider name. Gateway uses its own name for the key."""
    if provider == "gateway":
        return GATEWAY_API_KEY
    return PROVIDERS.get(provider, ("", ""))[1]


def model_route(model: str) -> tuple[str, str | None, str]:
    """Return (base_url, api_key, surface) for a model slug.

    ``surface`` is a provider name ("gateway", "nim", "openrouter") or "local".
    Callers use it to pick the wire format and to say which key is missing, so
    the format never depends on whether a key happens to be non-empty.
    """
    provider = provider_for(model)
    if provider == "gateway":
        return EXPLABS_URL, GATEWAY_API_KEY, "gateway"
    if provider:
        base_url, key = PROVIDERS[provider]
        return base_url, key, provider
    return OLLAMA_URL, None, "local"


# ---------------------------------------------------------------------------
# Trading desk
#
# Every value here is read through os.environ, so a real environment variable
# always wins over .env — the same contract as GATEWAY_API_KEY above. Nothing
# in this block is required: the desk runs paper-only with no keys at all.
# ---------------------------------------------------------------------------
DESK_DIR = ROOT / "desk"
DESK_DB = ROOT / "desk.db"
DESK_CACHE = DESK_DIR / "cache"
DESK_LOGS = DESK_DIR / "logs"
DESK_KILL_FILE = DESK_DIR / "KILL"
DESK_DIAL_FILE = DESK_DIR / "dial.json"


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


#: The trading judge uses OpenRouter by default. "socket" remains an explicit
#: legacy option; "ollama" selects the configured chat-model path.
DESK_BRAIN = _env_str("DESK_BRAIN", "openrouter").strip().lower()
#: One pinned judge, not the "openrouter/free" router: the router picks a
#: different model per call, so the same facts drew skip, hold and skip and no
#: record could say whether the judge helps. The fallback is the same model on
#: NIM, so a free-tier 429 or a cut-off reply does not swap in another opinion.
DESK_BRAIN_MODEL = _env_str("DESK_BRAIN_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
DESK_BRAIN_FALLBACK_MODEL = _env_str("DESK_BRAIN_FALLBACK_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")

#: "paper" is the default and is never inferred from whether a key is present.
DESK_EXECUTION_MODE = _env_str("DESK_EXECUTION_MODE", "paper").strip().lower()

#: Which broker backs the crypto venue: "jupiter" (self-custody Solana swaps) or
#: "robinhood" (a custodial account). Whichever is named still degrades to paper
#: without its credentials, so this only picks the live path.
DESK_CRYPTO_BROKER = _env_str("DESK_CRYPTO_BROKER", "jupiter").strip().lower()

DESK_START_CAPITAL = _env_float("DESK_START_CAPITAL", 10_000.0)
DESK_MAX_POSITION_PCT = _env_float("DESK_MAX_POSITION_PCT", 10.0)
DESK_MAX_POSITIONS = _env_int("DESK_MAX_POSITIONS", 8)
DESK_CONFIDENCE_FLOOR = _env_float("DESK_CONFIDENCE_FLOOR", 0.6)
DESK_LIQUIDITY_FLOOR = _env_float("DESK_LIQUIDITY_FLOOR", 50_000.0)
DESK_MIN_BARS = _env_int("DESK_MIN_BARS", 250)

#: The daily range a position is sized against. A name moving this much per day
#: gets the full budget; one moving twice as much gets half of it, so every
#: position risks roughly the same cash. Raw percentage, 4.0 == 4%.
DESK_REFERENCE_ATR_PCT = _env_float("DESK_REFERENCE_ATR_PCT", 4.0)

#: Seconds between cycles when the desk is left running in a front end.
DESK_CYCLE_SECONDS = _env_int("DESK_CYCLE_SECONDS", 60)

#: Risk ceilings. The kill switch trips on any one of them.
DESK_DAILY_LOSS_LIMIT_PCT = _env_float("DESK_DAILY_LOSS_LIMIT_PCT", 3.0)
DESK_MAX_DRAWDOWN_PCT = _env_float("DESK_MAX_DRAWDOWN_PCT", 15.0)
DESK_MAX_LOSING_DAYS = _env_int("DESK_MAX_LOSING_DAYS", 3)

#: Learning from the desk's own record. Once this many trades have closed, a
#: win rate below the floor shrinks the dial proportionally (never below a
#: quarter), and the tail of outcomes goes into the judge's evidence.
DESK_RECORD_MIN_TRADES = _env_int("DESK_RECORD_MIN_TRADES", 12)
DESK_RECORD_WIN_FLOOR = _env_float("DESK_RECORD_WIN_FLOOR", 0.40)
DESK_RECORD_WINDOW = _env_int("DESK_RECORD_WINDOW", 30)

#: Costs charged on both legs of every simulated fill. Venue-specific, because
#: a liquid ETF and a memecoin do not fill alike: the stock figures approximate
#: a commission-free broker's spread, the crypto figures assume thin books.
DESK_FEES_BPS = _env_float("DESK_FEES_BPS", 10.0)
DESK_SLIPPAGE_BPS = _env_float("DESK_SLIPPAGE_BPS", 20.0)
DESK_FEES_BPS_STOCKS = _env_float("DESK_FEES_BPS_STOCKS", 3.0)
DESK_SLIPPAGE_BPS_STOCKS = _env_float("DESK_SLIPPAGE_BPS_STOCKS", 3.0)
DESK_FEES_BPS_CRYPTO = _env_float("DESK_FEES_BPS_CRYPTO", DESK_FEES_BPS)
DESK_SLIPPAGE_BPS_CRYPTO = _env_float("DESK_SLIPPAGE_BPS_CRYPTO", 50.0)


def desk_costs(venue: str) -> tuple[float, float]:
    """``(fees_bps, slippage_bps)`` for a venue. Live brokers price themselves,
    so this only feeds the paper book."""
    if venue == "stocks":
        return DESK_FEES_BPS_STOCKS, DESK_SLIPPAGE_BPS_STOCKS
    return DESK_FEES_BPS_CRYPTO, DESK_SLIPPAGE_BPS_CRYPTO

#: Walk-forward gate thresholds. DESK_MIN_TRADES is deliberately high — it
#: will reject most candidates, which is the honest outcome.
DESK_MIN_TRADES = _env_int("DESK_MIN_TRADES", 30)
DESK_PF_MIN = _env_float("DESK_PF_MIN", 1.2)
DESK_MAX_DD = _env_float("DESK_MAX_DD", 0.25)
DESK_MAX_DEGRADATION = _env_float("DESK_MAX_DEGRADATION", 0.5)

DESK_UNIVERSE = [
    s.strip() for s in _env_str(
        # XLU, XLB, UNH and META joined after a 29-symbol search: they, with
        # MSFT, are the only names whose rule beat buy-and-hold on 2022-24.
        "DESK_UNIVERSE", "SPY,QQQ,AAPL,MSFT,NVDA,XLU,XLB,UNH,META,BTC-USD,ETH-USD"
    ).split(",")
    if s.strip()
]

#: Sector of each universe name, and how many open positions one sector may
#: hold. MSFT, META, NVDA, AAPL and QQQ all move with big tech; without a cap a
#: good week could fill every slot with what is really one bet. A symbol not in
#: the map is its own sector.
DESK_SECTORS = {
    "AAPL": "tech", "MSFT": "tech", "NVDA": "tech", "META": "tech", "QQQ": "tech",
    "SPY": "broad", "XLU": "utilities", "XLB": "materials", "UNH": "health",
    "BTC-USD": "crypto", "ETH-USD": "crypto",
}
DESK_MAX_PER_SECTOR = _env_int("DESK_MAX_PER_SECTOR", 2)
#: Core + satellite: this % of the stock venue is bought once in
#: DESK_CORE_SYMBOL and held; the desk trades the rest, and the kill switch
#: watches only that rest. 0 turns it off. Nothing tested beat holding SPY.
DESK_CORE_SYMBOL = _env_str("DESK_CORE_SYMBOL", "SPY")
DESK_CORE_PCT = _env_float("DESK_CORE_PCT", 0.0)

#: Anchored calendar split for the three verdicts. Bar history shorter than
#: this falls back to a proportional split (see desk_backtest.proportional_split).
DESK_TRAIN = ("2015-01-01", "2019-12-31")
DESK_VALIDATE = ("2020-01-01", "2021-12-31")
DESK_HOLDOUT = ("2022-01-01", "2024-12-31")
DESK_TRAIN_FRACTION = _env_float("DESK_TRAIN_FRACTION", 0.55)
DESK_VALIDATE_FRACTION = _env_float("DESK_VALIDATE_FRACTION", 0.20)

# Credentials. All optional; each venue degrades to paper without them.
ALPACA_KEY_ID = _env_str("ALPACA_KEY_ID", "")
ALPACA_SECRET_KEY = _env_str("ALPACA_SECRET_KEY", "")
ALPACA_PAPER = _env_str("ALPACA_PAPER", "1").strip().lower() not in {"0", "false", "no"}
#: Alpaca's paper host: real broker API, fake money. Fills there are recorded as
#: mode "broker-paper", never "live".
ALPACA_PAPER_URL = "https://paper-api.alpaca.markets"
_ALPACA_URL = _env_str("ALPACA_BASE_URL", "")
ALPACA_BASE_URL = _ALPACA_URL or (
    ALPACA_PAPER_URL if ALPACA_PAPER else "https://api.alpaca.markets"
)

SOLANA_RPC_URL = _env_str("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
SOLANA_PRIVATE_KEY = _env_str("SOLANA_PRIVATE_KEY", "")
#: Where the wallet lives if the key is kept in a file instead of .env. A
#: ``solana-keygen`` JSON file and a base58 text file are both accepted.
SOLANA_KEYPAIR_PATH = _env_str("SOLANA_KEYPAIR_PATH", "")
#: The public address. Optional; it lets the desk read a wallet that has no
#: key loaded (balances and positions, but no trading).
SOLANA_WALLET_ADDRESS = _env_str("SOLANA_WALLET_ADDRESS", "")

#: Jupiter aggregation. The v6 host is gone; this one is verified live.
JUPITER_API_URL = _env_str("JUPITER_API_URL", "https://lite-api.jup.ag/swap/v1")
#: Slippage tolerance, in basis points. Memecoins need more than equities.
SOLANA_SLIPPAGE_BPS = _env_int("SOLANA_SLIPPAGE_BPS", 100)
#: Lamports left alone so the wallet can always pay a fee and rent.
SOLANA_MIN_SOL = _env_float("SOLANA_MIN_SOL", 0.01)
#: Refuse a swap whose Jupiter price impact is worse than this.
SOLANA_MAX_PRICE_IMPACT_PCT = _env_float("SOLANA_MAX_PRICE_IMPACT_PCT", 5.0)
#: A quote older than this is re-fetched rather than reused for marking.
SOLANA_QUOTE_TTL_SECONDS = _env_int("SOLANA_QUOTE_TTL_SECONDS", 20)

GMGN_API_KEY = _env_str("GMGN_API_KEY", "")
GMGN_SECRET_KEY = _env_str("GMGN_SECRET_KEY", "")

# Robinhood Crypto Trading API. The API key is sent in plain text, but *every*
# request is signed with an Ed25519 key, so the private half is what makes the
# account usable — the key alone authenticates nothing. Generate the pair with
# ``python -m desk_keys`` (or any Ed25519 generator), upload the public half in
# the Robinhood app, and keep the base64 private half here.
ROBINHOOD_BASE_URL = _env_str("ROBINHOOD_BASE_URL", "https://trading.robinhood.com")
ROBINHOOD_API_KEY = _env_str("ROBINHOOD_API_KEY", "")
#: base64 of the Ed25519 private key seed.
ROBINHOOD_PRIVATE_KEY = _env_str("ROBINHOOD_PRIVATE_KEY", "")
