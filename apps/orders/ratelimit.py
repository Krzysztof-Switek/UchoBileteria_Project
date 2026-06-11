"""Tiny cache-based rate limiter for the purchase endpoint."""

from django.core.cache import cache

LIMIT = 10  # orders
WINDOW_SECONDS = 600  # per 10 minutes per client IP


def client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def is_rate_limited(request) -> bool:
    key = f"purchase-rate:{client_ip(request)}"
    added = cache.add(key, 1, timeout=WINDOW_SECONDS)
    if added:
        return False
    try:
        count = cache.incr(key)
    except ValueError:  # key expired between add and incr
        cache.add(key, 1, timeout=WINDOW_SECONDS)
        return False
    return count > LIMIT
