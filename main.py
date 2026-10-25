import argparse
import sys
from pathlib import Path

import config
import preflight
from orchestrator import Orchestrator
from config import WORKSPACE


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local planner/coder/reviewer team through Ollama.")
    parser.add_argument("request", nargs="*", help="What you want the AI team to build")
    parser.add_argument("--auto-approve", action="store_true", help="Apply changes without prompting")
    parser.add_argument("--fast", action="store_true", help="Use the smaller 4B model for non-reviewer roles")
    parser.add_argument("--quality-rounds", type=int, default=1, choices=range(0, 3), help="Extra review/fix rounds (0-2)")
    parser.add_argument("--project", type=Path, default=WORKSPACE, help="Project folder agents may read/write")
    parser.add_argument("--web", action="store_true", help="Add a web research brief to planning")
    parser.add_argument("--auto-web", action="store_true", help="Research automatically when the request needs current sources")
    parser.add_argument("--preflight", action="store_true", help="Show model availability report and exit")
    parser.add_argument("--auto-models", action="store_true", help="Automatically use local model fallbacks when gateway key is missing")
    parser.add_argument("--desk", action="store_true", help="Run the trading desk directly instead of classifying intent")
    parser.add_argument("--cycles", type=int, default=1, help="Desk cycles to run (with --desk)")
    parser.add_argument("--venue", action="append", choices=["stocks", "crypto"], help="Desk venue(s); repeatable (with --desk)")
    parser.add_argument("--desk-mode", choices=["paper", "shadow", "live"], default=None, help="paper, shadow (real Robinhood reads, simulated orders) or live (with --desk)")
    parser.add_argument("--desk-limit", type=int, default=12, help="Max candidates per venue (with --desk)")
    args = parser.parse_args()

    # Handle --preflight
    if args.preflight:
        statuses = preflight.preflight()
        print(preflight.format_report(statuses))
        sys.exit(0)

    # If no request given and not preflight, error
    if not args.request and not args.desk:
        parser.error("the following arguments are required: request")

    # Handle --auto-models: swap in local models for any seat whose provider has
    # no key. Asking preflight rather than testing one key keeps this in step
    # with wherever the seats are actually routed — they no longer all sit on
    # the gateway, so a gateway-only check would never fire.
    if args.auto_models and any(
        status.status == "no-key" for status in preflight.preflight()
    ):
        suggested = preflight.suggest_config()
        if suggested:
            config.MODELS.update(suggested)
        else:
            print("[Aether] No provider key and no local model; keeping the config.")

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

    # --desk bypasses intent classification entirely: no model call, so it
    # works with every model down.
    if args.desk:
        print(orch.run_desk(cycles=args.cycles, venues=args.venue,
                            mode=args.desk_mode, scan_limit=args.desk_limit))
        return

    result = orch.run(request)

    if result.mode == "desk":
        print(result.desk_report or result.error or "(desk produced no report)")
        return

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