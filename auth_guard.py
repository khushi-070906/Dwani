"""Small protections for the website's login: a limit on wrong passwords, and the response headers every page gets.

Kept in its own file (no database, no web framework) so it can be tested on its own.

The limit lives in this process's memory. The site runs as one process on Render, so that is one shared count; if it is
ever run as several workers each keeps its own, and the limit becomes "per worker", which still stops guessing."""
import time
from collections import deque


class FailureLimiter:
    """At most `limit` failures per key in any `window` seconds; after that the key is refused until the oldest failure ages
    out. A success clears the key, so a person who finally types it right is not punished for the earlier tries."""

    def __init__(self, limit: int, window: float, max_keys: int = 20000, clock=time.monotonic):
        self.limit, self.window, self.max_keys, self.clock = limit, window, max_keys, clock
        self._fails: dict[str, deque] = {}

    def _recent(self, key: str) -> deque:
        q = self._fails.get(key)
        if q is None:
            return deque()
        cutoff = self.clock() - self.window
        while q and q[0] <= cutoff:
            q.popleft()
        if not q:
            self._fails.pop(key, None)
        return q

    def retry_after(self, key: str) -> int:
        """0 when the key may try; otherwise whole seconds to wait."""
        q = self._recent(key)
        if len(q) < self.limit:
            return 0
        return max(1, int(q[0] + self.window - self.clock()) + 1)

    def record_failure(self, key: str) -> None:
        if len(self._fails) >= self.max_keys and key not in self._fails:
            self._forget_expired()
            if len(self._fails) >= self.max_keys:       # still full: drop the oldest key rather than grow without end
                self._fails.pop(next(iter(self._fails)))
        self._fails.setdefault(key, deque()).append(self.clock())
        self._recent(key)

    def clear(self, key: str) -> None:
        self._fails.pop(key, None)

    def _forget_expired(self) -> None:
        for k in list(self._fails):
            self._recent(k)


# Wrong passwords: tight for one account (someone guessing at it), looser for one address (a shared office or school
# network where several people make ordinary mistakes).
LOGIN_PER_EMAIL = FailureLimiter(limit=8, window=15 * 60)
LOGIN_PER_ADDRESS = FailureLimiter(limit=40, window=15 * 60)

# No page of this site is meant to be shown inside another site, and none guesses its own type.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}
