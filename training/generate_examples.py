import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from agents import ask_json, coder, parse_proposal

OUTPUT = Path(__file__).parent / "candidates.jsonl"
PROMPTS = [
    "Create a Python function that validates an email address and include unittest tests.",
    "Create a Python JSON configuration loader with defaults and tests for a missing file.",
    "Create a Python function that converts CSV text into a list of dictionaries and test it.",
    "Create a Python retry decorator with a maximum attempt count and tests.",
    "Create a Python command-line argument parser for a notes app and include tests.",
    "Create a Python function that safely joins a project root and relative path, rejecting traversal.",
    "Create a Python in-memory todo service with add, complete, delete, and tests.",
    "Create a Python function that summarizes word frequencies in text and test punctuation handling.",
    "Create a Python HTTP client wrapper with a timeout and a mocked test.",
    "Create a Python log formatter that emits JSON and test required fields.",
    "Create a Python function that clamps a number between a minimum and maximum and include unittest tests.",
    "Create a Python function that groups records by a key and include unittest tests for empty input.",
    "Create a Python function that parses ISO date strings and returns UTC-aware datetimes with tests.",
    "Create a Python LRU cache class using only the standard library and include unittest tests.",
    "Create a Python function that redacts API keys in log messages and include unittest tests.",
    "Create a Python function that atomically writes text to a file and include tests using a temporary directory.",
    "Create a Python function that merges nested dictionaries without mutating either input and include tests.",
    "Create a Python function that validates a port number and include unittest tests for boundary values.",
]

CODER_INSTRUCTIONS = (
    "Implement the request with complete files and executable unittest tests. "
    "Use type hints, docstrings, robust input validation, and non-contradictory behavior. "
    "Do not include demo code in library modules. Return ONLY the requested JSON object."
)


def generate_proposal(request: str) -> tuple[dict | None, str | None]:
    """Generate one proposal, retrying malformed model output without aborting the batch."""
    raw = coder(request, CODER_INSTRUCTIONS, "(empty workspace)")
    last_error = "unknown parse error"
    for attempt in range(3):
        try:
            return parse_proposal(raw), None
        except (ValueError, json.JSONDecodeError, TypeError, KeyError) as exc:
            last_error = str(exc)
            if attempt == 2:
                break
            raw = ask_json(
                "coder",
                'Return ONLY valid JSON with this exact shape: '
                '{"summary":"...","files":[{"path":"relative/path","content":"complete file content"}]}. '
                "Escape newlines and quotes inside strings. No markdown fences, commentary, or extra keys are required.",
                "The previous response was invalid. Repair it while preserving the implementation:\n" + raw,
            )
            time.sleep(0.2)
    return None, last_error


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--fresh", action="store_true", help="Ignore previously generated requests and select new ones")
    args = parser.parse_args()
    existing = OUTPUT.read_text(encoding="utf-8").splitlines() if OUTPUT.exists() else []
    existing_requests = set()
    for line in existing:
        try:
            item = json.loads(line)
            existing_requests.add(item.get("request", ""))
        except json.JSONDecodeError:
            continue
    available = [prompt for prompt in PROMPTS if args.fresh or prompt not in existing_requests]
    prompts = available[:max(1, min(args.count, len(available)))]
    if not prompts:
        raise SystemExit("No new prompts remain. Add prompts to PROMPTS or omit --fresh.")
    with OUTPUT.open("a", encoding="utf-8") as handle:
        for number, request in enumerate(prompts, 1):
            print(f"[{number}/{len(prompts)}] Generating: {request}")
            proposal, error = generate_proposal(request)
            if proposal is None:
                print(f"  SKIPPED: {error}")
                handle.write(json.dumps({
                    "request": request,
                    "proposal": None,
                    "approved": False,
                    "status": "generation_failed",
                    "error": error,
                }) + "\n")
                continue
            handle.write(json.dumps({"request": request, "proposal": proposal, "approved": False, "status": "pending"}) + "\n")
    print(f"Candidates saved to {OUTPUT}. Review them with approve_examples.py.")


if __name__ == "__main__":
    main()
