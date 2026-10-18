"""
Text-to-speech for Aether using Windows SAPI (built-in, no extra install).
Provides J.A.R.V.I.S.-style voice output.
"""

from __future__ import annotations

import threading
from typing import Optional


class Voice:
    """Windows SAPI text-to-speech wrapper. Runs speaking on a background thread."""

    def __init__(self, enabled: bool = False, rate: int = 0, volume: int = 100):
        self.enabled = enabled
        self.rate = rate  # -10 to 10
        self.volume = volume  # 0 to 100
        self._speaker = None
        self._lock = threading.Lock()
        self._init_speaker()

    def _init_speaker(self) -> None:
        try:
            import win32com.client  # type: ignore
            self._speaker = win32com.client.Dispatch("SAPI.SpVoice")
            self._speaker.Rate = self.rate
            self._speaker.Volume = self.volume
        except Exception:  # noqa: BLE001
            self._speaker = None
            self.enabled = False

    def speak(self, text: str) -> None:
        """Speak text asynchronously (non-blocking)."""
        if not self.enabled or not self._speaker or not text.strip():
            return
        def _run() -> None:
            with self._lock:
                try:
                    self._speaker.Speak(text, 1)  # 1 = async
                except Exception:  # noqa: BLE001
                    pass
        threading.Thread(target=_run, daemon=True).start()

    def speak_sync(self, text: str) -> None:
        """Speak text synchronously (blocking)."""
        if not self.enabled or not self._speaker or not text.strip():
            return
        with self._lock:
            try:
                self._speaker.Speak(text, 0)  # 0 = sync
            except Exception:  # noqa: BLE001
                pass

    def toggle(self) -> bool:
        """Toggle voice on/off. Returns new state."""
        self.enabled = not self.enabled
        return self.enabled
