"""Tiny cache-based rate limiters for order-related public endpoints."""

from django.core.cache import cache

LIMIT = 10  # orders
WINDOW_SECONDS = 600  # per 10 minutes per client IP

RESEND_COOLDOWN_SECONDS = 60  # per order (KUP-03)


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


def resend_is_cooling_down(order_id) -> bool:
    """One "wyślij ponownie" click per order per cooldown window — a repeat
    click (or reload) shouldn't queue duplicate ticket e-mails."""
    key = f"resend-tickets:{order_id}"
    added = cache.add(key, 1, timeout=RESEND_COOLDOWN_SECONDS)
    return not added
