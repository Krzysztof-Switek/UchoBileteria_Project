"""Cache-based brute-force protection for the staff login form (public-facing)."""

from django.core.cache import cache

from apps.orders.ratelimit import client_ip

MAX_FAILURES = 5
WINDOW_SECONDS = 900  # 15 minutes


def _key(request, identifier: str) -> str:
    return f"login-fail:{client_ip(request)}:{identifier.strip().lower()}"


def is_login_rate_limited(request, identifier: str) -> bool:
    return cache.get(_key(request, identifier), 0) >= MAX_FAILURES


def register_login_failure(request, identifier: str) -> None:
    key = _key(request, identifier)
    added = cache.add(key, 1, timeout=WINDOW_SECONDS)
    if not added:
        try:
            cache.incr(key)
        except ValueError:  # key expired between add and incr
            cache.add(key, 1, timeout=WINDOW_SECONDS)


def clear_login_failures(request, identifier: str) -> None:
    cache.delete(_key(request, identifier))


def handle_successful_login(sender, request, user, **kwargs) -> None:
    """Reset the throttle for whichever identifier the person typed to log in."""
    if request is None:
        return
    for identifier in filter(None, {user.get_username(), user.email}):
        clear_login_failures(request, identifier)
