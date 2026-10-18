"""
Aether Memory Vault — plain-text/.md files the AI reads at the start of every
conversation and writes to as it works, so it never forgets your project.

Inspired by Jared Rhod's AI Memory Vault: your AI's memory is just files you
can open in Obsidian, Notepad, or any text editor. Nothing is hidden.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

VAULT_DIR = Path(__file__).parent / "vault"

VAULT_INDEX = """# Aether Memory Vault

This is Aether's long-term memory. Each `.md` file here is read by Aether at the
start of every conversation and written to as work progresses. You can open,
edit, or delete any of these files in Obsidian, Notepad, or any text editor.

## Files

| File | Purpose |
|------|---------|
| `user-preferences.md` | Your preferences, rules, and working style |
| `project-context.md` | Current project goals, status, and next steps |
| `conversation-summary.md` | Summary of recent conversations |
| `learnings.md` | Things Aether has learned about your setup |
| `todo.md` | Outstanding tasks and priorities |

## How it works

- Aether reads all files in this folder before responding to you.
- Aether can append to or create files when it learns something useful.
- Nothing is hidden — you own and control every file here.
"""

USER_PREFERENCES = """# User Preferences

Your working style, rules, and preferences for Aether.

## Add your preferences here

- Preferred communication style:
- Default project folder:
- Programming languages you use:
- Models you prefer:
- Safety rules (what Aether should never do):
"""

PROJECT_CONTEXT = """# Project Context

Current project goals, status, and next steps.

## Active project

None yet. Tell Aether what you're working on and it will record it here.

## Recent accomplishments

None yet.

## Next steps

None yet.
"""

CONVERSATION_SUMMARY = """# Conversation Summary

Summary of recent conversations with Aether.

## Latest conversation

None yet.
"""

LEARNINGS = """# Learnings

Things Aether has learned about your setup, preferences, and workflow.

## Notes

None yet.
"""

TODO = """# Todo

Outstanding tasks and priorities.

## Today

- [ ] Tell Aether what you'd like to work on first.

## Later

None yet.
"""


def ensure_vault() -> None:
    """Create the vault folder and default files if they don't exist."""
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    defaults = {
        "README.md": VAULT_INDEX,
        "user-preferences.md": USER_PREFERENCES,
        "project-context.md": PROJECT_CONTEXT,
        "conversation-summary.md": CONVERSATION_SUMMARY,
        "learnings.md": LEARNINGS,
        "todo.md": TODO,
    }
    for filename, content in defaults.items():
        path = VAULT_DIR / filename
        if not path.exists():
            path.write_text(content, encoding="utf-8")


def read_vault() -> str:
    """Read all vault files and return them as a single context block."""
    ensure_vault()
    parts = ["--- Aether Memory Vault ---"]
    for path in sorted(VAULT_DIR.glob("*.md")):
        parts.append(f"\n### {path.name}\n{path.read_text(encoding='utf-8')}")
    return "\n".join(parts)


def read_file(filename: str) -> Optional[str]:
    """Read a single vault file by name."""
    path = VAULT_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def write_file(filename: str, content: str) -> str:
    """Write content to a vault file, creating it if needed. Returns the path."""
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    path = VAULT_DIR / filename
    if not path.suffix:
        path = path.with_suffix(".md")
    path.write_text(content, encoding="utf-8")
    return str(path)


def append_file(filename: str, content: str) -> str:
    """Append content to a vault file. Returns the path."""
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    path = VAULT_DIR / filename
    if not path.suffix:
        path = path.with_suffix(".md")
    with path.open("a", encoding="utf-8") as f:
        f.write(content if content.endswith("\n") else content + "\n")
    return str(path)


def list_files() -> list[str]:
    """List all markdown files in the vault."""
    ensure_vault()
    return sorted(p.name for p in VAULT_DIR.glob("*.md"))
