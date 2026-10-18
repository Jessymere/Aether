from datetime import datetime
from pathlib import Path
import shutil


def append_patch_note(root: Path, request: str, files: list[str], summary: str = "") -> None:
    """Record an approved change in the project's persistent patch history."""
    changelog = root / "CHANGELOG.md"
    if not changelog.exists():
        changelog.write_text("# Aether Patch Notes\n\n", encoding="utf-8")
    note = [
        f"## {datetime.now():%Y-%m-%d %H:%M}",
        f"- Request: {request.strip()}",
        f"- Files: {', '.join(files) or '(none)' }",
    ]
    if summary.strip():
        note.append(f"- Summary: {summary.strip()}")
    with changelog.open("a", encoding="utf-8") as handle:
        handle.write("\n" + "\n".join(note) + "\n")


def backup_files(root: Path, files: list[str]) -> Path | None:
    """Copy existing files to a timestamped rollback folder before overwriting them."""
    existing = [Path(path) for path in files if (root / path).is_file()]
    if not existing:
        return None
    backup_root = root / ".aether" / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    for relative in existing:
        target = backup_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, target)
    return backup_root


def restore_latest_backup(root: Path) -> Path | None:
    """Restore the newest backup and return its folder, or None when no backup exists."""
    backups = root / ".aether" / "backups"
    candidates = sorted((path for path in backups.iterdir() if path.is_dir()), reverse=True) if backups.exists() else []
    if not candidates:
        return None
    selected = candidates[0]
    for source in selected.rglob("*"):
        if source.is_file():
            relative = source.relative_to(selected)
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return selected
