import argparse
import difflib
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from agents import coder, parse_proposal, planner, reviewer, revise, tester, use_fast_models, workspace_context
from config import ROOT
from loop_guard import LoopGuard
from patch_notes import append_patch_note
from proposal_validation import validate_proposal

ALLOWED_SUFFIXES = {".py", ".md", ".Modelfile"}


def backup_project() -> Path:
    backup_dir = ROOT / "backups"
    backup_dir.mkdir(exist_ok=True)
    target = backup_dir / f"builder-{datetime.now():%Y%m%d-%H%M%S}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in ROOT.rglob("*"):
            if path.is_file() and "backups" not in path.parts and "runs" not in path.parts:
                archive.write(path, path.relative_to(ROOT))
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Have the local AI team propose a guarded improvement to itself.")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--rounds", type=int, default=1, choices=(0, 1, 2))
    parser.add_argument("--restart", action="store_true", help="Restart the desktop UI after approved changes")
    parser.add_argument("--auto-approve", action="store_true", help="Apply changes without prompting")
    args = parser.parse_args()
    if args.fast:
        use_fast_models()

    request = (
        "Improve this local multi-AI builder itself. Inspect the existing Python code, desktop UI, "
        "Ollama client, and documentation. Preserve the approval gate, project-folder sandbox, "
        "path safety, and local-only behavior. Make only concrete improvements that increase "
        "reliability, usability, speed, testing, or model coordination. Do not add network services, "
        "secret access, unrestricted shell execution, self-deletion, or automatic approval. Only modify "
        "Python, Markdown, or Modelfile files."
    )
    context = workspace_context(ROOT)
    print("[1/4] Inspecting the builder...")
    plan = planner(request, context)
    print(plan)
    print("\n[2/4] Proposing improvements...")
    raw = coder(request, plan, context)
    proposal = parse_proposal(raw)
    for item in proposal["files"]:
        if Path(item["path"]).suffix not in ALLOWED_SUFFIXES:
            raise ValueError(f"Self-improvement can only change {ALLOWED_SUFFIXES}: {item['path']}")
    print(proposal.get("summary", "Proposal ready."))
    print("\n[3/4] Reviewing improvements...")
    review = reviewer(request, plan, raw)
    print(review)
    loop_guard = LoopGuard()
    for number in range(args.rounds):
        if "REVISE" not in review.upper():
            break
        if not loop_guard.allow("revision", json.dumps(proposal, sort_keys=True) + review):
            print("\n[quality round] Loop guard stopped a repeated identical revision.")
            break
        print(f"\n[quality round {number + 1}] Repairing proposal...")
        raw = revise(request, plan, json.dumps(proposal), review)
        proposal = parse_proposal(raw)
        review = reviewer(request, plan, raw)
        print(review)

    print("\n[4/4] Validating proposal in sandbox...")
    validation_ok, validation_msg = validate_proposal(proposal)
    print(validation_msg)
    if not validation_ok:
        print("Self-improvement rejected: sandbox validation failed. No changes made.")
        return
    tester_report = tester(request, json.dumps(proposal), validation_msg)
    print(f"\nTest agent report:\n{tester_report}")

    # Diff preview
    print("\n=== Diff Preview ===")
    for item in proposal["files"]:
        target = ROOT / item["path"]
        old = target.read_text(encoding="utf-8") if target.exists() else ""
        diff = "\n".join(difflib.unified_diff(
            old.splitlines(), item["content"].splitlines(),
            fromfile=str(target), tofile=str(target) + " (proposed)", lineterm=""
        )) or "(no textual changes)"
        print(f"\n--- {item['path']} ---\n{diff}")

    if not args.auto_approve:
        answer = input("\nApply this self-improvement proposal? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("No changes made.")
            return
    backup = backup_project()
    for item in proposal["files"]:
        target = (ROOT / item["path"]).resolve()
        if ROOT.resolve() not in target.parents or target.suffix not in ALLOWED_SUFFIXES:
            raise ValueError(f"Unsafe self-improvement target: {item['path']}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item["content"], encoding="utf-8")
    print(f"Applied {len(proposal['files'])} file(s). Backup saved to {backup}")
    from patch_notes import append_patch_note
    append_patch_note(
        ROOT,
        request,
        [item["path"] for item in proposal["files"]],
        proposal.get("summary", ""),
    )
    from orchestrator import RUNS
    from datetime import datetime as _dt
    RUNS.mkdir(parents=True, exist_ok=True)
    run_path = RUNS / f"{_dt.now():%Y%m%d-%H%M%S-%f}.json"
    run_path.write_text(json.dumps({
        "request": request,
        "plan": plan,
        "proposal": proposal,
        "review": review,
        "validation": validation_msg,
        "tester_report": tester_report,
        "approved": True,
        "self_improvement": True,
    }, indent=2), encoding="utf-8")
    if args.restart:
        subprocess.Popen([sys.executable, str(ROOT / "desktop_ui.py")], cwd=str(ROOT))
        os._exit(0)


if __name__ == "__main__":
    main()
