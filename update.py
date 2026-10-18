"""
Update checker for Aether desktop app.

Fetches a version manifest from your own server and compares against the
local VERSION. If a newer version is available, returns the download info.
The actual download and replace is manual (Windows file-lock prevents
in-place self-overwrite).
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Optional

from config import UPDATE_MANIFEST_URL, VERSION


@dataclass
class UpdateInfo:
    available: bool
    version: str = ""
    download_url: str = ""
    notes: str = ""
    current_version: str = VERSION


def check_for_updates() -> UpdateInfo:
    """Check the hosted manifest for a newer version.

    Returns UpdateInfo with available=True if a newer version exists.
    Never raises — failures return available=False.
    """
    try:
        req = urllib.request.Request(
            UPDATE_MANIFEST_URL,
            headers={"User-Agent": f"Aether/{VERSION}"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read().decode())
        remote_version = payload.get("version", "").strip()
        if remote_version and remote_version != VERSION:
            return UpdateInfo(
                available=True,
                version=remote_version,
                download_url=payload.get("download_url", ""),
                notes=payload.get("notes", ""),
            )
    except Exception:  # noqa: BLE001
        pass
    return UpdateInfo(available=False, current_version=VERSION)
