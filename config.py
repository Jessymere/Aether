import os
from pathlib import Path

OLLAMA_URL = "http://127.0.0.1:11434"
APP_NAME = "Aether"
MODELS = {
    "planner": "qwen3.8-27b",
    "coder": "deepseek-v4.1-flash-uncensored",
    "reviewer": "deepseek-v4.1-flash-1017",
    "tester": "qwen3.8-27b",
    "security": "qwen3.8-27b",
    "release": "qwen3.8-27b",
    "decider": "gpt-6-luna-decisions",
    "deepseek_coder": "deepseek-v4.1-flash-uncensored",
    "deepseek_coder_1017": "deepseek-v4.1-flash-1017",
    "qwen_coder": "qwen3.8-27b",
    "vision": "gemma4:e4b",
    "link": "qwen3.5:4b",
}
FAST_MODELS = {
    "planner": "qwen3.5:4b",
    "coder": "qwen3.5:4b",
    "reviewer": "qwen3.5:4b",
    "tester": "qwen3.5:4b",
    "security": "qwen3.5:4b",
    "release": "qwen3.5:4b",
    "deepseek_coder": "qwen3.5:4b",
    "deepseek_coder_1017": "qwen3.5:4b",
    "qwen_coder": "qwen3.5:4b",
    "decider": "qwen3.5:4b",
    "vision": "qwen3.5:4b",
    "link": "qwen3.5:4b",
}
ROOT = Path(__file__).parent
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
EXPLABS_API_KEY = os.environ.get("EXPLABS_API_KEY", "")
EXPLABS_MODELS = {
    "qwen_coder": "qwen3.8-27b",
    "deepseek_coder": "deepseek-v4.1-flash-uncensored",
    "deepseek_coder_1017": "deepseek-v4.1-flash-1017",
    "decider": "gpt-6-luna-decisions",
}
EXPLABS_CHAT_PATH = "/v1/chat/completions"

# Experiential Labs Decisions gateway
EXPERIENTIAL_API_KEY = os.environ.get("EXPERIENTIAL_API_KEY", "")
DECISIONS_URL = "https://api.experientiallabs.ai"
DECISIONS_PATH = "/v1/decisions"
DECISION_MODEL = "gpt-6-luna-decisions"
