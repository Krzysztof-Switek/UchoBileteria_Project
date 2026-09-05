"""Admin-triggered refunds (spec section 14). MVP: full-order refunds only."""

from django.core.exceptions import ValidationError

from apps.auditlog.services import log_action
from apps.events.models import Event
from apps.events.services import cancel_event

from .models import Order, OrderStatus
from .providers import get_provider
from .services import cancel_unpaid_order


def refund_order(order: Order, actor=None, reason: str = "") -> str:
    """
    Request a full refund from the order's own provider (a demo order never
    touches Stripe). The actual status change happens when the provider's
    refund webhook arrives — for DemoProvider that is synchronous.
    """
    if order.status != OrderStatus.PAID:
        raise ValidationError("Zwrócić można tylko opłacone zamówienie.")
    log_action("refund.requested", order, actor=actor,
               metadata={"amount": str(order.amount_gross), "reason": reason})
    return get_provider(order).refund(order)


def cancel_event_with_refunds(event: Event, actor=None) -> dict:
    """
    One-click event cancellation: cancel the event, refund every paid order,
    cancel every order still awaiting payment.
    """
    cancel_event(event, actor=actor)

    refunded = failed = cancelled = 0
    for order in event.orders.filter(status=OrderStatus.PAID):
        try:
            refund_order(order, actor=actor)
            refunded += 1
        except Exception:  # noqa: BLE001 - keep refunding the rest; admin sees the count
            failed += 1
    for order in event.orders.filter(
        status__in=[OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING]
    ):
        if cancel_unpaid_order(order):
            cancelled += 1

    summary = {"refunded": refunded, "failed": failed, "cancelled_unpaid": cancelled}
    log_action("event.cancelled_with_refunds", event, actor=actor, metadata=summary)
    return summary
