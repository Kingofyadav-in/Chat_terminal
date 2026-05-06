from client.config import RECONNECT_DELAYS


def backoff_delay(attempt: int) -> int:
    """Return the reconnect delay for a zero-based attempt number."""
    if attempt < 0:
        attempt = 0
    return RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
