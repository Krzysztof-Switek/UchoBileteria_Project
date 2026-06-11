"""
Provider-agnostic webhook processing (spec section 13.1).

Idempotency: every webhook is recorded in `payment_events` with a unique
(provider, provider_event_id) pair *before* any side effect. A duplicate
insert means the event was already handled and the call becomes a no-op.
"""

from django.db import IntegrityError, models, transaction
from django.utils import timezone

from apps.auditlog.services import log_action
from apps.events.models import Event, TicketPool

from .models import Order, OrderStatus, PaymentEvent, PaymentEventStatus


class SignatureError(Exception):
    """Webhook signature verification failed."""


def process_webhook(provider: str, payload: dict, payload_hash: str = "") -> PaymentEvent:
    """Record and process a verified webhook payload. Safe to call twice."""
    order = Order.objects.filter(id=payload.get("order_id")).first()

    try:
        with transaction.atomic():
            record = PaymentEvent.objects.create(
                provider=provider,
                provider_event_id=payload["id"],
                event_type=payload.get("type", ""),
                order=order,
                amount=payload.get("amount") or None,
                is_demo=order.is_demo if order else True,
                payload_hash=payload_hash,
            )
    except IntegrityError:
        # Same provider event already recorded -> duplicate delivery, no-op.
        return PaymentEvent.objects.get(
            provider=provider, provider_event_id=payload["id"]
        )

    if order is None:
        _finish(record, PaymentEventStatus.ERROR)
        return record

    event_type = payload.get("type", "")
    if event_type == "payment.succeeded":
        ok = mark_order_paid(order)
        _finish(record, PaymentEventStatus.PROCESSED if ok else PaymentEventStatus.IGNORED)
    elif event_type == "payment.failed":
        ok = mark_order_failed(order)
        _finish(record, PaymentEventStatus.PROCESSED if ok else PaymentEventStatus.IGNORED)
    elif event_type == "refund.succeeded":
        ok = mark_order_refunded(order)
        _finish(record, PaymentEventStatus.PROCESSED if ok else PaymentEventStatus.IGNORED)
    else:
        _finish(record, PaymentEventStatus.IGNORED)
    return record


def _finish(record: PaymentEvent, status: str) -> None:
    record.processing_status = status
    record.processed_at = timezone.now()
    record.save(update_fields=["processing_status", "processed_at"])


def mark_order_paid(order: Order) -> bool:
    """
    PAYMENT_PENDING/CREATED -> PAID. For an EXPIRED order (late webhook) try to
    re-reserve capacity; when the event meanwhile sold out the order stays
    EXPIRED and the caller's payment event is IGNORED (admin sees it and the
    refund flow returns the money).
    """
    now = timezone.now()
    claimed = Order.objects.filter(
        pk=order.pk, status__in=[OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING]
    ).update(status=OrderStatus.PAID, paid_at=now)
    if claimed:
        order.refresh_from_db()
        log_action("order.paid", order, metadata={"amount": str(order.amount_gross)})
        _issue_tickets_if_configured(order)
        return True

    order.refresh_from_db()
    if order.status == OrderStatus.EXPIRED:
        if _try_rereserve(order):
            Order.objects.filter(pk=order.pk, status=OrderStatus.EXPIRED).update(
                status=OrderStatus.PAID, paid_at=now
            )
            order.refresh_from_db()
            log_action("order.paid_late", order, metadata={"rereserved": True})
            _issue_tickets_if_configured(order)
            return True
        log_action("order.late_payment_no_capacity", order)
        return False
    return False  # already PAID / REFUNDED / CANCELLED -> nothing to do


def _try_rereserve(order: Order) -> bool:
    """Atomically take the seats back for a late-paid expired order."""
    with transaction.atomic():
        pool_ok = TicketPool.objects.filter(
            pk=order.pool_id, sold_count__lte=models.F("capacity") - order.quantity
        ).update(sold_count=models.F("sold_count") + order.quantity)
        if not pool_ok:
            return False
        event_ok = Event.objects.filter(
            pk=order.event_id, sold_total__lte=models.F("capacity_total") - order.quantity
        ).update(sold_total=models.F("sold_total") + order.quantity)
        if not event_ok:
            transaction.set_rollback(True)
            return False
    return True


def mark_order_failed(order: Order) -> bool:
    """PAYMENT_PENDING -> FAILED and release the reserved capacity."""
    with transaction.atomic():
        claimed = Order.objects.filter(
            pk=order.pk, status__in=[OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING]
        ).update(status=OrderStatus.FAILED)
        if not claimed:
            return False
        TicketPool.objects.filter(pk=order.pool_id).update(
            sold_count=models.F("sold_count") - order.quantity
        )
        Event.objects.filter(pk=order.event_id).update(
            sold_total=models.F("sold_total") - order.quantity
        )
    order.refresh_from_db()
    log_action("order.payment_failed", order)
    return True


def mark_order_refunded(order: Order) -> bool:
    """PAID -> REFUNDED. Ticket invalidation lives in the refund service."""
    claimed = Order.objects.filter(pk=order.pk, status=OrderStatus.PAID).update(
        status=OrderStatus.REFUNDED, refunded_at=timezone.now()
    )
    if not claimed:
        return False
    order.refresh_from_db()
    log_action("order.refunded", order, metadata={"amount": str(order.amount_gross)})
    _invalidate_tickets_after_refund(order)
    return True


def _issue_tickets_if_configured(order: Order) -> None:
    """Hook for ticket issuing (implemented in apps.tickets, Phase 6)."""
    try:
        from apps.tickets.services import issue_tickets_for_order
    except ImportError:
        return
    issue_tickets_for_order(order)


def _invalidate_tickets_after_refund(order: Order) -> None:
    try:
        from apps.tickets.services import invalidate_tickets_for_refunded_order
    except ImportError:
        return
    invalidate_tickets_for_refunded_order(order)
