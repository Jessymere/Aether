import json
import argparse
import hashlib
import sys
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
CANDIDATES = HERE / "candidates.jsonl"
DATASET = HERE / "dataset.jsonl"
sys.path.insert(0, str(HERE.parent))
from agents import parse_proposal, revise, reviewer


def reviewer_approved(review: str) -> bool:
    """Accept explicit APPROVE verdicts at either the start or end of a review."""
    lines = [line.strip().upper() for line in review.splitlines() if line.strip()]
    if not lines:
        return False
    first = lines[0]
    last = lines[-1]
    if first.startswith("REVISE") or first.startswith("REJECT") or last.startswith("REVISE") or last.startswith("REJECT"):
        return False
    return first.startswith("APPROVE") or last.startswith("APPROVE")


def validate_proposal(proposal: dict) -> str:
    with tempfile.TemporaryDirectory(prefix="local-ai-training-") as folder:
        root = Path(folder)
        for item in proposal["files"]:
            target = (root / item["path"]).resolve()
            if root.resolve() not in target.parents:
                return f"Unsafe path: {item['path']}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"], encoding="utf-8")
        compile_result = subprocess.run([sys.executable, "-m", "compileall", "-q", "."], cwd=root, capture_output=True, text=True, timeout=30)
        if compile_result.returncode:
            return "Compilation failed:\n" + (compile_result.stdout + compile_result.stderr)
        test_result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-v"], cwd=root, capture_output=True, text=True, timeout=60)
        if test_result.returncode:
            return "Tests failed:\n" + (test_result.stdout + test_result.stderr)
    return "PASS"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto-approve", action="store_true", help="Approve only candidates whose reviewer ends with APPROVE")
    parser.add_argument("--auto-fix", action="store_true", help="Repair rejected candidates and review them again, up to four times")
    args = parser.parse_args()
    if not CANDIDATES.exists():
        raise SystemExit("No candidates.jsonl found. Run generate_examples.py first.")
    approved = []
    updated_candidates = []
    for line in CANDIDATES.read_text(encoding="utf-8").splitlines():
        candidate = json.loads(line)
        if candidate.get("status") == "approved":
            updated_candidates.append(candidate)
            continue
        if not isinstance(candidate.get("proposal"), dict):
            candidate["status"] = candidate.get("status", "generation_failed")
            updated_candidates.append(candidate)
            print(f"\nSKIP (no proposal): {candidate['request']}\n  {candidate.get('error', 'unknown generation failure')}")
            continue
        print("\nREQUEST:\n" + candidate["request"])
        print("FILES: " + ", ".join(item["path"] for item in candidate["proposal"]["files"]))
        answer = "n"
        if args.auto_approve or args.auto_fix:
            plan = "Implement the request with complete files and tests."
            proposal = candidate["proposal"]
            review = ""
            for attempt in range(4):
                test_result = validate_proposal(proposal)
                print(f"VALIDATION (attempt {attempt + 1}): {test_result}")
                if test_result != "PASS":
                    if not args.auto_fix:
                        break
                    repair_feedback = "Automated validation failed. Fix the implementation and tests.\n" + test_result
                    try:
                        proposal = parse_proposal(revise(candidate["request"], plan, json.dumps(proposal), repair_feedback))
                    except (ValueError, json.JSONDecodeError, TypeError, KeyError) as exc:
                        review = f"Repair response was invalid: {exc}"
                        break
                    continue
                review = reviewer(candidate["request"], plan, json.dumps(proposal))
                print(f"REVIEW (attempt {attempt + 1}):\n{review}")
                if reviewer_approved(review):
                    break
                if not args.auto_fix:
                    break
                repair_feedback = review + "\n\nAutomated validation result:\n" + test_result
                try:
                    proposal = parse_proposal(revise(candidate["request"], plan, json.dumps(proposal), repair_feedback))
                except (ValueError, json.JSONDecodeError, TypeError, KeyError) as exc:
                    review = f"Repair response was invalid: {exc}"
                    break
            final_validation = validate_proposal(proposal)
            candidate.update({"proposal": proposal, "review": review, "validation": final_validation})
            if reviewer_approved(review) and final_validation == "PASS":
                candidate["status"] = "approved"
                candidate["proposal"] = proposal
                answer = "y"
        else:
            answer = input("Approve this training example? [y/N] ").strip().lower()
        if answer in {"y", "yes"}:
            approved.append({
                "messages": [
                    {"role": "system", "content": "You are a careful local coding agent. Return only valid JSON with summary and complete relative-path files."},
                    {"role": "user", "content": candidate["request"]},
                    {"role": "assistant", "content": json.dumps(candidate["proposal"], ensure_ascii=False)},
                ]
            })
        updated_candidates.append(candidate)

    CANDIDATES.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in updated_candidates) + "\n", encoding="utf-8")
    existing_hashes = set()
    if DATASET.exists():
        for line in DATASET.read_text(encoding="utf-8").splitlines():
            existing_hashes.add(hashlib.sha256(line.encode()).hexdigest())
    with DATASET.open("a", encoding="utf-8") as handle:
        for item in approved:
            encoded = json.dumps(item, ensure_ascii=False)
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            if digest not in existing_hashes:
                handle.write(encoded + "\n")
                existing_hashes.add(digest)
    print(f"Added {len(approved)} approved example(s) to {DATASET}")


if __name__ == "__main__":
    main()
