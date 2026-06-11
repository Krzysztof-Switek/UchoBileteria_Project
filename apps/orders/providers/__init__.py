from apps.orders.models import Order, PaymentProviderKind

from .base import PaymentProvider
from .demo import DemoProvider
from .stripe_provider import StripeProvider


def get_provider(order: Order) -> PaymentProvider:
    """Return the provider that owns this order. Demo orders never touch Stripe."""
    if order.payment_provider == PaymentProviderKind.DEMO:
        return DemoProvider()
    if order.payment_provider == PaymentProviderKind.STRIPE:
        return StripeProvider()
    raise NotImplementedError(
        f"Provider {order.payment_provider} nie jest jeszcze obsługiwany."
    )
