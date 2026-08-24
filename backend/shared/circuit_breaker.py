"""
Thread-safe circuit breaker for external API calls (Groq, Jina, etc.).

States:
  CLOSED    — normal; requests pass through
  OPEN      — failure threshold hit; requests blocked for cooldown_s seconds
  HALF_OPEN — cooldown expired; one probe request allowed;
              success → CLOSED, failure → resets cooldown and stays OPEN

Usage:
    _cb = CircuitBreaker('groq', failure_threshold=5, cooldown_s=300)

    if not _cb.allow():
        return None          # fail fast, don't call the API
    try:
        result = api_call()
        _cb.record_success()
        return result
    except Exception as e:
        _cb.record_failure()
        raise
"""
import logging
import threading
import time

log = logging.getLogger(__name__)


class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 5, cooldown_s: int = 300):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self._lock = threading.Lock()
        self._failures = 0
        self._state = 'CLOSED'    # 'CLOSED' | 'OPEN' | 'HALF_OPEN'
        self._opened_at: float = 0.0
        self._probe_in_flight = False

    def allow(self) -> bool:
        """Return True if a request should be allowed through right now."""
        with self._lock:
            if self._state == 'CLOSED':
                return True

            if self._state == 'OPEN':
                if time.time() - self._opened_at >= self.cooldown_s:
                    # Cooldown expired — send one probe request
                    if not self._probe_in_flight:
                        self._state = 'HALF_OPEN'
                        self._probe_in_flight = True
                        log.info(
                            f"[circuit_breaker] {self.name} → HALF_OPEN "
                            f"(cooldown expired, sending probe)"
                        )
                        return True
                return False   # still cooling down

            # HALF_OPEN: only the one probe already in flight
            return False

    def record_success(self) -> None:
        with self._lock:
            if self._state != 'CLOSED':
                log.info(f"[circuit_breaker] {self.name} → CLOSED (probe succeeded)")
            self._failures = 0
            self._state = 'CLOSED'
            self._probe_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._probe_in_flight = False
            should_open = (
                self._state in ('HALF_OPEN', 'OPEN')
                or self._failures >= self.failure_threshold
            )
            if should_open:
                self._state = 'OPEN'
                self._opened_at = time.time()
                log.warning(
                    f"[circuit_breaker] {self.name} → OPEN after {self._failures} "
                    f"consecutive failures — blocking calls for {self.cooldown_s}s"
                )

    def status(self) -> dict:
        """Snapshot for health checks — safe to call at any time."""
        with self._lock:
            remaining = 0
            if self._state == 'OPEN':
                remaining = max(0.0, self.cooldown_s - (time.time() - self._opened_at))
            return {
                'state': self._state,
                'consecutive_failures': self._failures,
                'cooldown_remaining_s': int(remaining),
            }
