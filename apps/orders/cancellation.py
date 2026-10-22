"""
Cancelling an event with sold tickets (decisions of 06.10.2026):

- every buyer of a paid order gets an e-mail;
- without a new date -> the money goes back at once, to the payment method the
  buyer used (card, BLIK, transfer) — the payment operator does it, the club
  never stores card or bank account data;
- with a new date of the same show -> the buyer chooses on the order page:
  keep the tickets for the new date (same codes; on a seated event the same
  seats if free) or a refund; no answer by the deadline = automatic refund;
- a refund the operator could not make (e.g. a very old payment) is marked
  REFUND_FAILED and listed for manual handling;
- every step is written to the audit log of the order (who, what, when).

Polish consumer law: for a cancelled or postponed event the buyer may always
demand a refund — the club may not set a date after which the money is lost,
so "no answer = refund", never "no answer = ticket expires".
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.auditlog.services import log_action
from apps.events.models import Event, EventStatus, TicketPool

from .models import CancellationStatus, Order, OrderStatus
from .refunds import refund_order
from .services import cancel_unpaid_order, release_capacity

DEFAULT_DECISION_DAYS = 14
# Terms of sale § 5: the buyer gets at least 7 days to choose, unless the new
# date itself comes sooner (then until the day before it).
MIN_DECISION_DAYS = 7


def default_deadline(replacement: Event | None, now=None):
    """14 days, but never later than the day before the new date."""
    now = now or timezone.now()
    deadline = now + timedelta(days=DEFAULT_DECISION_DAYS)
    if replacement is not None:
        deadline = min(deadline, replacement.start_at - timedelta(days=1))
    return deadline


def _when(dt) -> str:
    return timezone.localtime(dt).strftime("%d.%m.%Y, godz. %H:%M")


def default_message(event: Event, replacement: Event | None = None, new_start=None) -> str:
    """The polite part of the buyer's e-mail, editable on the cancel screen.
    The facts (amount, buttons, deadline, tickets) are always added by the
    e-mail template itself, so the manager cannot delete them by accident."""
    new_start = replacement.start_at if replacement is not None else new_start
    if new_start is None:
        return (
            f"Z przykrością informujemy, że wydarzenie „{event.title}” zaplanowane na "
            f"{_when(event.start_at)} zostało odwołane. Bardzo przepraszamy za kłopot.\n\n"
            "Pieniądze za bilety oddajemy w całości — szczegóły poniżej."
        )
    return (
        f"Informujemy, że wydarzenie „{event.title}” zmienia termin: zamiast "
        f"{_when(event.start_at)} odbędzie się {_when(new_start)}. Bardzo przepraszamy "
        "za zmianę planów.\n\n"
        "Szczegóły i wybór — zachowanie biletów albo zwrot — poniżej."
    )


def cancellation_summary(event: Event) -> dict:
    paid = event.orders.filter(status=OrderStatus.PAID)
    return {
        "paid_orders": paid.count(),
        "tickets": sum(o.active_quantity for o in paid),
        "amount": sum((o.kept_amount for o in paid), start=0),
        "unpaid_orders": event.orders.filter(
            status__in=[OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING]).count(),
    }


def replacement_candidates(event: Event):
    return (Event.objects.filter(start_at__gt=timezone.now(),
                                 status__in=[EventStatus.DRAFT, EventStatus.PUBLISHED])
            .exclude(pk=event.pk).order_by("start_at"))


def cancel_event_for_buyers(event: Event, actor=None, *, reason: str = "",
                            replacement: Event | None = None, deadline=None) -> dict:
    """Cancel the event and tell every buyer; refunds at once when there is
    no new date. Returns counts for the manager."""
    from apps.events.services import cancel_event

    if replacement is not None:
        if replacement.pk == event.pk or replacement.start_at <= timezone.now():
            raise ValidationError("Nowy termin musi być innym, przyszłym wydarzeniem.")
        deadline = deadline or default_deadline(replacement)
        now = timezone.now()
        latest = replacement.start_at - timedelta(days=1)
        if deadline <= now:
            raise ValidationError("Termin decyzji kupujących musi być w przyszłości.")
        if deadline > latest:
            raise ValidationError("Termin decyzji musi być najpóźniej dzień przed nowym terminem.")
        if deadline < now + timedelta(days=MIN_DECISION_DAYS) and deadline < latest:
            raise ValidationError(f"Kupujący mają co najmniej {MIN_DECISION_DAYS} dni na decyzję "
                                  "(regulamin § 5), chyba że nowy termin jest wcześniej.")
    cancel_event(event, actor=actor)
    # no edits on the cancel screen -> the default (automatic) wording goes out
    message = reason.strip()[:4000] or default_message(event, replacement)
    Event.objects.filter(pk=event.pk).update(
        cancelled_at=timezone.now(), cancellation_reason=message,
        replacement_event=replacement, decision_deadline=deadline if replacement else None,
    )
    event.refresh_from_db()

    result = {"notified": 0, "refunded": 0, "refund_failed": 0, "cancelled_unpaid": 0}
    for order in event.orders.filter(status__in=[OrderStatus.CREATED,
                                                  OrderStatus.PAYMENT_PENDING]):
        if cancel_unpaid_order(order):
            result["cancelled_unpaid"] += 1
    from apps.tickets.services import queue_event_cancelled_email

    for order in event.orders.filter(status=OrderStatus.PAID):
        if replacement is not None:
            Order.objects.filter(pk=order.pk).update(
                cancellation_status=CancellationStatus.AWAITING)
            order.refresh_from_db()
        queue_event_cancelled_email(order)
        log_action("cancellation.notified", order, actor=actor, metadata={
            "replacement_id": replacement.pk if replacement else None,
            "deadline": deadline.isoformat() if replacement and deadline else None,
        })
        result["notified"] += 1
        if replacement is None:
            if request_refund(order, actor=actor, source="odwołanie bez nowego terminu"):
                result["refunded"] += 1
            else:
                result["refund_failed"] += 1
    log_action("event.cancelled_for_buyers", event, actor=actor, metadata={
        **result, "replacement_id": replacement.pk if replacement else None,
        "reason": reason[:200],
    })
    return result


def request_refund(order: Order, actor=None, source: str = "") -> bool:
    """Ask the payment operator for the refund (to the original payment
    method). False = the operator refused — the order waits for manual
    handling and the reason is in the audit log."""
    try:
        refund_order(order, actor=actor, reason=f"Odwołane wydarzenie: {source}")
    except Exception as exc:  # noqa: BLE001 - every failure is logged and listed
        Order.objects.filter(pk=order.pk).update(
            cancellation_status=CancellationStatus.REFUND_FAILED,
            cancellation_decided_at=timezone.now())
        log_action("cancellation.refund_failed", order, actor=actor,
                   metadata={"source": source, "error": str(exc)[:300]})
        return False
    Order.objects.filter(pk=order.pk).update(
        cancellation_status=CancellationStatus.REFUND_REQUESTED,
        cancellation_decided_at=timezone.now())
    log_action("cancellation.refund_requested", order, actor=actor, metadata={"source": source})
    return True


def _target_pools(order: Order, target: Event) -> list[TicketPool]:
    """Same pool name first, then the same price, then the rest."""
    pools = list(target.pools.order_by("sales_start_at", "pk"))
    name = order.pool.name
    price = order.pool.price_gross
    return sorted(pools, key=lambda p: (p.name != name, p.price_gross != price))


def move_to_replacement(order: Order, actor=None) -> Order:
    """The buyer keeps the tickets for the new date: same codes, same amount
    paid; on a seated new date the same seats if free, otherwise free seats
    of the same table/row first, then any free seats."""
    from apps.seating.models import EventSeat, SeatStatus
    from apps.tickets.models import Ticket, TicketStatus

    order.refresh_from_db()
    old_event = order.event
    target = old_event.replacement_event
    if order.cancellation_status != CancellationStatus.AWAITING or target is None:
        raise ValidationError("To zamówienie nie czeka na wybór nowego terminu.")
    if order.status != OrderStatus.PAID:
        raise ValidationError("Zamówienie nie jest opłacone.")
    if target.status not in (EventStatus.DRAFT, EventStatus.PUBLISHED):
        raise ValidationError("Nowy termin nie jest już dostępny — wybierz zwrot.")
    tickets = list(Ticket.objects.filter(order=order, status=TicketStatus.ISSUED)
                   .select_related("seat").order_by("pk"))
    n = len(tickets)
    if n == 0:
        raise ValidationError("W zamówieniu nie ma ważnych biletów.")

    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.cancellation_status != CancellationStatus.AWAITING:
            raise ValidationError("Decyzja dla tego zamówienia została już podjęta.")
        # the cancelled date first: counters and seats back (before the new
        # seats get this order, or they would be released too)
        release_capacity(order)
        pool = None
        for candidate in _target_pools(order, target):
            if TicketPool.objects.filter(
                pk=candidate.pk, sold_count__lte=models.F("capacity") - n
            ).update(sold_count=models.F("sold_count") + n):
                pool = candidate
                break
        if pool is None or not Event.objects.filter(
            pk=target.pk, sold_total__lte=models.F("capacity_total") - n
        ).update(sold_total=models.F("sold_total") + n):
            raise ValidationError("Na nowym terminie zabrakło miejsc — wybierz zwrot.")

        new_seats = []
        if target.is_seated:
            wanted = [t.seat.seat_key for t in tickets if t.seat_id]
            free = list(EventSeat.objects.select_for_update()
                        .filter(event=target, status=SeatStatus.AVAILABLE)
                        .order_by("sort_index"))
            by_key = {s.seat_key: s for s in free}
            picked = [by_key.pop(k) for k in wanted if k in by_key]
            groups = {s.group_key for s in picked}
            rest = sorted(by_key.values(), key=lambda s: (s.group_key not in groups,
                                                          s.sort_index))
            picked += rest[: n - len(picked)]
            if len(picked) < n:
                raise ValidationError("Na nowym terminie zabrakło wolnych miejsc — wybierz zwrot.")
            new_seats = picked
            EventSeat.objects.filter(pk__in=[s.pk for s in picked]).update(
                status=SeatStatus.SOLD, order=order)

        for index, ticket in enumerate(tickets):
            ticket.event = target
            ticket.pool = pool
            ticket.seat = new_seats[index] if new_seats else None
        Ticket.objects.bulk_update(tickets, ["event", "pool", "seat"])
        order.moved_from_event = old_event
        order.event = target
        order.pool = pool
        order.seat_keys = [s.seat_key for s in new_seats]
        order.seat_labels = [s.label for s in new_seats]
        order.cancellation_status = CancellationStatus.MOVED
        order.cancellation_decided_at = timezone.now()
        order.save(update_fields=["moved_from_event", "event", "pool", "seat_keys",
                                  "seat_labels", "cancellation_status",
                                  "cancellation_decided_at"])
    log_action("cancellation.moved", order, actor=actor, metadata={
        "from_event": old_event.pk, "to_event": target.pk, "tickets": n,
        "seats": order.seat_labels,
    })
    from apps.tickets.services import queue_ticket_email

    queue_ticket_email(order, tickets)
    return order


def choose_refund(order: Order, actor=None) -> bool:
    order.refresh_from_db()
    if order.cancellation_status != CancellationStatus.AWAITING:
        raise ValidationError("Decyzja dla tego zamówienia została już podjęta.")
    log_action("cancellation.buyer_chose_refund", order, actor=actor)
    return request_refund(order, actor=actor, source="wybór kupującego")


def refund_now(orders, actor=None) -> dict:
    """The manager does not wait for the deadline: refund these orders at once
    (buyers still deciding, or a refund that failed before — retry)."""
    result = {"refunded": 0, "refund_failed": 0}
    for order in orders:
        order.refresh_from_db()
        if order.status != OrderStatus.PAID or order.cancellation_status not in (
            CancellationStatus.AWAITING, CancellationStatus.REFUND_FAILED
        ):
            continue
        log_action("cancellation.manager_refund", order, actor=actor,
                   metadata={"previous": order.cancellation_status})
        if request_refund(order, actor=actor, source="decyzja obsługi"):
            result["refunded"] += 1
        else:
            result["refund_failed"] += 1
    return result


# --- evidence: what the buyer was told and what happened, with dates -------------

ACTION_LABELS = {
    "order.created": "Zamówienie złożone",
    "order.paid": "Płatność potwierdzona przez operatora",
    "cancellation.notified": "Wysłano informację o odwołaniu",
    "cancellation.buyer_chose_refund": "Kupujący wybrał zwrot",
    "cancellation.moved": "Kupujący zachował bilety na nowy termin",
    "cancellation.deadline_passed": "Minął termin decyzji — zwrot automatyczny",
    "cancellation.manager_refund": "Obsługa zleciła zwrot",
    "cancellation.refund_requested": "Zwrot zlecony operatorowi płatności",
    "cancellation.refund_failed": "Operator odrzucił zwrot",
    "refund.requested": "Żądanie zwrotu wysłane do operatora",
    "refund.completed": "Zwrot potwierdzony przez operatora",
    "order.refunded": "Zwrot potwierdzony przez operatora",
}


def order_evidence(order: Order) -> list[dict]:
    """Time-ordered record for one order — every e-mail (with the exact text
    sent and the time it left the system), every decision and every message
    from the payment operator. Proof for a buyer who missed the e-mail and
    turns up with a refunded ticket."""
    from apps.auditlog.models import AuditLog
    from apps.tickets.models import EmailOutbox

    from .models import PaymentEvent

    rows = []
    for log in AuditLog.objects.filter(entity_id=str(order.pk)).order_by("created_at"):
        rows.append({"at": log.created_at, "kind": "log",
                     "title": ACTION_LABELS.get(log.action, log.action),
                     "who": log.actor_label or "system", "details": log.metadata})
    for mail in EmailOutbox.objects.filter(order=order).order_by("created_at"):
        rows.append({"at": mail.created_at, "kind": "email", "title": f"E-mail: {mail.subject}",
                     "who": mail.to_email, "sent_at": mail.sent_at,
                     "status": mail.get_status_display(), "body": mail.body_text})
    for payment in PaymentEvent.objects.filter(order=order).order_by("received_at"):
        rows.append({"at": payment.received_at, "kind": "payment",
                     "title": f"Operator płatności: {payment.event_type}",
                     "who": payment.provider, "amount": payment.amount,
                     "details": {"id": payment.provider_event_id,
                                 "status": payment.get_processing_status_display()}})
    return sorted(rows, key=lambda r: r["at"])


def refund_proof_line(ticket) -> str:
    """One line for the gate: when the refund happened and when the buyer was
    e-mailed about it (the scanner shows it next to "Bilet zwrócony")."""
    from apps.tickets.models import EmailOutbox

    order = ticket.order
    parts = []
    if order.refunded_at:
        parts.append(f"zwrot {timezone.localtime(order.refunded_at):%d.%m.%Y}")
    mails = EmailOutbox.objects.filter(order=order, sent_at__isnull=False).filter(
        models.Q(subject__startswith="Wydarzenie odwołane")
        | models.Q(subject__startswith="Zmiana terminu")
        | models.Q(subject__startswith="Zwrot")).order_by("sent_at")
    if mails:
        parts.append("e-mail do kupującego wysłany "
                     + ", ".join(f"{timezone.localtime(m.sent_at):%d.%m %H:%M}" for m in mails))
    return ("Dowód: " + "; ".join(parts) + ".") if parts else ""


def process_decision_deadlines(now=None) -> dict:
    """Cron: no answer by the deadline -> automatic refund."""
    now = now or timezone.now()
    due = Order.objects.filter(
        cancellation_status=CancellationStatus.AWAITING,
        event__decision_deadline__lte=now,
    )
    result = {"refunded": 0, "refund_failed": 0}
    for order in due:
        log_action("cancellation.deadline_passed", order)
        if request_refund(order, source="brak decyzji w terminie"):
            result["refunded"] += 1
        else:
            result["refund_failed"] += 1
    return result
