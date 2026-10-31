"""Utility helpers shared across modules.

Provides the `retry` decorator used for transient‑failure retries.
"""

import logging
import time
from functools import wraps
from typing import Callable, Tuple

def retry(attempts: int = 3, backoff: float = 1.0, allowed_exceptions: tuple = (Exception,)):
    """Simple retry decorator with exponential backoff.

    Example::
        @retry(attempts=5, backoff=2)
        def fragile_operation(...):
            ...
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = backoff
            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except allowed_exceptions as e:
                    if attempt == attempts:
                        raise
                    logging.warning(
                        "Attempt %d/%d failed for %s: %s. Retrying in %.1fs...",
                        attempt,
                        attempts,
                        func.__name__,
                        e,
                        delay,
                    )
                    time.sleep(delay)
                    delay *= 2
        return wrapper
    return decorator
