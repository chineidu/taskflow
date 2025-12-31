import threading

from slowapi import Limiter
from slowapi.util import get_remote_address

from src import create_logger

logger = create_logger(name="rate_limit")


class RateLimiter:
    """Thread-safe lazy initializer for rate limiter."""

    def __init__(self) -> None:
        self._limiter: Limiter | None = None
        self._is_initialized: bool = False
        # Thread-safe initialization (prevents race conditions)
        self._lock: threading.Lock = threading.Lock()

    def initialize(self) -> None:
        """Initialize the rate limiter if not already initialized."""
        if self._is_initialized:
            return

        with self._lock:
            if not (self._limiter and self._is_initialized):
                self._limiter = self._setup_rate_limiter()
                self._is_initialized = True
                logger.info("🔔 Rate limiter initialized.")
        return

    def get_limiter(self) -> Limiter | None:
        """Get the rate limiter."""
        if not self._is_initialized or self._limiter is None:
            self.initialize()

        return self._limiter

    # opposite of initialize
    def shutdown(self) -> None:
        """Shutdown the rate limiter."""
        if not self._is_initialized:
            return

        with self._lock:
            self._limiter = None
            self._is_initialized = False
            logger.info("🚨 Rate limiter shutdown.")
        return

    def _setup_rate_limiter(self) -> Limiter:
        """
        Initialize rate limiter (call once at startup).
        Uses client IP address as the key for rate limiting.
        """
        return Limiter(key_func=get_remote_address)


limiter: Limiter = RateLimiter().get_limiter()  # type: ignore
