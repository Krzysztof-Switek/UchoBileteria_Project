"""Admin-triggered refunds (spec section 14): the whole order, or selected tickets."""

from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError

from apps.auditlog.services import log_action
from apps.events.models import Event
from apps.events.services import cancel_event

from .models import Order, OrderRefund, OrderStatus, RefundStatus
from .providers import get_provider
from .services import cancel_unpaid_order


def refund_order(order: Order, actor=None, reason: str = "") -> str:
    """
    Request a full refund from the order's own provider (a demo order never
    touches Stripe). The actual status change happens when the provider's
    refund webhook arrives — for DemoProvider that is synchronous.
    """
    order.refresh_from_db()
    if order.status != OrderStatus.PAID:
        raise ValidationError("Zwrócić można tylko opłacone zamówienie.")
    remaining = order.amount_gross - order.refunded_amount
    log_action("refund.requested", order, actor=actor,
               metadata={"amount": str(remaining), "reason": reason})
    if order.refunded_amount:
        return get_provider(order).refund(order, amount=remaining)
    return get_provider(order).refund(order)


def refundable_tickets(order: Order):
    """Tickets of a paid order that can still be refunded one by one: issued,
    not used at the door, not already in a pending partial refund."""
    from apps.tickets.models import TicketStatus

    pending = set()
    for refund in order.refunds.filter(status=RefundStatus.REQUESTED):
        pending.update(refund.ticket_ids)
    return [
        t for t in order.tickets.filter(status=TicketStatus.ISSUED).select_related("seat")
        if str(t.id) not in pending
    ]


def refund_tickets(order: Order, tickets, actor=None, reason: str = ""):
    """
    Refund selected tickets of a paid order (e.g. one friend can't come).
    The price is the order's per-ticket price; the last tickets get exactly the
    rest of the amount (no rounding leftovers). Refunding every remaining
    ticket of an untouched order is simply a full refund.
    """
    order.refresh_from_db()  # counters may have moved since the caller loaded it
    if order.status != OrderStatus.PAID:
        raise ValidationError("Zwrócić można tylko bilety opłaconego zamówienia.")
    allowed = {t.id: t for t in refundable_tickets(order)}
    chosen = [allowed.get(getattr(t, "id", t)) for t in tickets]
    if not chosen or any(t is None for t in chosen):
        raise ValidationError(
            "Wybierz bilety tego zamówienia, które nie zostały jeszcze wykorzystane ani zwrócone."
        )
    count = len(chosen)
    pending = order.refunds.filter(status=RefundStatus.REQUESTED)
    pending_qty = sum(r.quantity for r in pending)
    pending_amount = sum((r.amount for r in pending), Decimal("0"))
    left_after = order.quantity - order.refunded_quantity - pending_qty - count
    if left_after == 0 and order.refunded_quantity == 0 and not pending_qty:
        return refund_order(order, actor=actor, reason=reason)

    if left_after == 0:
        amount = order.amount_gross - order.refunded_amount - pending_amount
    elif all(t.price_gross is not None for t in chosen):
        amount = sum((t.price_gross for t in chosen), Decimal("0"))  # zone prices differ
    else:
        amount = (order.amount_gross * count / order.quantity).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    refund = OrderRefund.objects.create(
        order=order,
        ticket_ids=[str(t.id) for t in chosen],
        ticket_codes=[t.short_code for t in chosen],
        seat_labels=[t.seat.label for t in chosen if t.seat_id],
        quantity=count,
        amount=amount,
        reason=reason,
        requested_by=actor if (actor is not None and actor.pk) else None,
    )
    log_action("refund.partial_requested", order, actor=actor, metadata={
        "amount": str(amount), "tickets": refund.ticket_codes, "reason": reason,
    })
    try:
        reference = get_provider(order).refund(order, amount=amount, order_refund=refund)
    except Exception:
        OrderRefund.objects.filter(pk=refund.pk, status=RefundStatus.REQUESTED).update(
            status=RefundStatus.FAILED
        )
        raise
    OrderRefund.objects.filter(pk=refund.pk).update(provider_refund_id=str(reference or ""))
    refund.refresh_from_db()
    return refund


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
