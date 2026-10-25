"""Windows notifications for the events worth interrupting someone for.

A buy, a sell, and the kill switch. Shown through Windows' own toast API from a
detached PowerShell, so nothing is installed and the desk never waits on it;
any failure is swallowed, because an alert must never break a cycle.
"""

from __future__ import annotations

import subprocess
import sys

from config import _env_str

#: "0" turns alerts off (tests and headless runs).
DESK_ALERTS = _env_str("DESK_ALERTS", "1").strip() != "0"

_TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode($env:AETHER_TITLE)) | Out-Null
$x.Item(1).AppendChild($t.CreateTextNode($env:AETHER_BODY)) | Out-Null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""


def notify(title: str, body: str) -> bool:
    """Show a toast. Returns whether one was attempted. Never raises."""
    if not DESK_ALERTS or sys.platform != "win32":
        return False
    try:
        import os
        # Text travels in environment variables, never spliced into the script,
        # so a symbol or reason cannot inject PowerShell.
        env = {**os.environ, "AETHER_TITLE": title[:120], "AETHER_BODY": body[:240]}
        subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except Exception:  # noqa: BLE001 — an alert must never break a cycle
        return False
