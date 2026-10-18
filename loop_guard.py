import hashlib


class LoopGuard:
    """Detect repeated agent actions and stop after a small safe limit."""

    def __init__(self, max_repeats: int = 2):
        self.max_repeats = max_repeats
        self._seen: dict[str, int] = {}

    def allow(self, action: str, payload: str) -> bool:
        fingerprint = hashlib.sha256(f"{action}\n{payload}".encode("utf-8", errors="replace")).hexdigest()
        count = self._seen.get(fingerprint, 0) + 1
        self._seen[fingerprint] = count
        return count <= self.max_repeats
