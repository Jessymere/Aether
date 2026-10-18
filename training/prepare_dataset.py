import json
import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).parents[1]
RUNS = ROOT / "runs"
OUTPUT = Path(__file__).parent / "dataset.jsonl"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-examples", type=int, default=10)
    args = parser.parse_args()
    examples = []
    seen = set()
    for path in sorted(RUNS.glob("*.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        if not run.get("approved"):
            continue
        proposal = run.get("proposal")
        if not proposal or not proposal.get("files"):
            continue
        example = {
            "messages": [
                {"role": "system", "content": "You are a careful local coding agent. Return only valid JSON with summary and complete relative-path files."},
                {"role": "user", "content": f"Request:\n{run['request']}\n\nPlan:\n{run.get('plan', '')}"},
                {"role": "assistant", "content": json.dumps(proposal, ensure_ascii=False)},
            ]
        }
        digest = hashlib.sha256(json.dumps(example, sort_keys=True).encode("utf-8")).hexdigest()
        if digest not in seen:
            examples.append(example)
            seen.add(digest)
    if len(examples) < args.min_examples:
        print(f"Only {len(examples)} unique approved examples found; keeping existing dataset instead of replacing it.")
        return
    OUTPUT.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in examples) + ("\n" if examples else ""), encoding="utf-8")
    print(f"Wrote {len(examples)} approved examples to {OUTPUT}")
    if len(examples) < 20:
        print("Warning: collect more approved examples before training; a tiny dataset can cause overfitting.")


if __name__ == "__main__":
    main()
