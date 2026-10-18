import argparse
import sys
from pathlib import Path

from orchestrator import Orchestrator
from config import WORKSPACE


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local planner/coder/reviewer team through Ollama.")
    parser.add_argument("request", nargs="+", help="What you want the AI team to build")
    parser.add_argument("--auto-approve", action="store_true", help="Apply changes without prompting")
    parser.add_argument("--fast", action="store_true", help="Use the smaller 4B model for non-reviewer roles")
    parser.add_argument("--quality-rounds", type=int, default=1, choices=range(0, 3), help="Extra review/fix rounds (0-2)")
    parser.add_argument("--project", type=Path, default=WORKSPACE, help="Project folder agents may read/write")
    parser.add_argument("--web", action="store_true", help="Add a web research brief to planning")
    parser.add_argument("--auto-web", action="store_true", help="Research automatically when the request needs current sources")
    args = parser.parse_args()

    request = " ".join(args.request)
    project = args.project.resolve()

    def log(message: str) -> None:
        print(f"[Aether] {message}")

    orch = Orchestrator(
        project=project,
        auto_approve=args.auto_approve,
        fast=args.fast,
        quality_rounds=args.quality_rounds,
        web=args.web,
        auto_web=args.auto_web,
        log_cb=log,
    )

    result = orch.run(request)

    if result.mode in ("chat", "question"):
        print(result.error)  # conversational reply stored in error field
        return

    if result.mode == "system":
        print(result.error)  # system response stored in error field
        return

    if result.error:
        if result.error == "Cancelled":
            print("Run cancelled.")
        else:
            print(f"Error: {result.error}", file=sys.stderr)
        return

    print("\n=== PLAN ===")
    print(result.plan or "(no plan)")
    print("\n=== PROPOSED FILES ===")
    print(result.proposal.get("summary", "Proposal ready") if result.proposal else "(no proposal)")
    for item in (result.proposal or {}).get("files", []):
        print(f"  - {item['path']}")

    if not args.auto_approve:
        answer = input("\nApply these files to project? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("No files were changed.")
            return

    orch.apply_proposal(result)
    print(f"\nApplied {len(result.files_written)} file(s) to {project}")


if __name__ == "__main__":
    main()
