"""Entrance control: atomic check-in and the emergency CSV list (spec 15-16)."""

import csv

from django.db.models import Q
from django.utils import timezone

from apps.auditlog.services import log_action
from apps.events.models import Event
from apps.tickets.models import Ticket, TicketStatus
from apps.tickets.services import hash_token


class ScanResult:
    """Spec 15.2 scan results."""

    VALID_CHECKED_IN = "VALID_CHECKED_IN"
    ALREADY_CHECKED_IN = "ALREADY_CHECKED_IN"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"
    INVALID_TOKEN = "INVALID_TOKEN"
    WRONG_EVENT = "WRONG_EVENT"
    SERVER_ERROR = "SERVER_ERROR"

    MESSAGES = {
        VALID_CHECKED_IN: "Bilet ważny — wpuść!",
        ALREADY_CHECKED_IN: "Bilet już wykorzystany!",
        REFUNDED: "Bilet zwrócony — wejście nieważne.",
        CANCELLED: "Bilet anulowany — wejście nieważne.",
        INVALID_TOKEN: "Nieznany bilet (błędny kod QR).",
        WRONG_EVENT: "Bilet na inne wydarzenie!",
        SERVER_ERROR: "Błąd systemu — spróbuj ponownie lub użyj listy awaryjnej.",
    }


def _attempt_check_in(ticket: Ticket, event: Event, staff) -> str:
    """Common path once a ticket has been located."""
    if ticket.event_id != event.pk:
        return ScanResult.WRONG_EVENT

    claimed = Ticket.objects.filter(pk=ticket.pk, status=TicketStatus.ISSUED).update(
        status=TicketStatus.CHECKED_IN,
        checked_in_at=timezone.now(),
        checked_in_by=staff if (staff is not None and staff.pk) else None,
    )
    if claimed:
        log_action("ticket.checked_in", ticket, actor=staff)
        return ScanResult.VALID_CHECKED_IN

    # Lost the race or ticket is in a terminal state — report why.
    ticket.refresh_from_db()
    return {
        TicketStatus.CHECKED_IN: ScanResult.ALREADY_CHECKED_IN,
        TicketStatus.REFUNDED: ScanResult.REFUNDED,
        TicketStatus.CANCELLED: ScanResult.CANCELLED,
        TicketStatus.INVALIDATED: ScanResult.CANCELLED,
    }.get(ticket.status, ScanResult.SERVER_ERROR)


def check_in_by_token(raw_token: str, event: Event, staff=None) -> tuple[str, Ticket | None]:
    ticket = Ticket.objects.filter(qr_token_hash=hash_token(raw_token)).first()
    if ticket is None:
        return ScanResult.INVALID_TOKEN, None
    return _attempt_check_in(ticket, event, staff), ticket


def check_in_by_short_code(code: str, event: Event, staff=None) -> tuple[str, Ticket | None]:
    ticket = Ticket.objects.filter(short_code__iexact=code.strip()).first()
    if ticket is None:
        return ScanResult.INVALID_TOKEN, None
    return _attempt_check_in(ticket, event, staff), ticket


def undo_check_in(ticket: Ticket, staff=None) -> bool:
    """OBS-06: reverses a check-in made by mistake. Only takes effect while
    the ticket is still CHECKED_IN — a ticket refunded/invalidated since
    the scan is left alone (nothing to undo back to)."""
    reverted = Ticket.objects.filter(pk=ticket.pk, status=TicketStatus.CHECKED_IN).update(
        status=TicketStatus.ISSUED,
        checked_in_at=None,
        checked_in_by=None,
    )
    if reverted:
        log_action("ticket.check_in_undone", ticket, actor=staff)
    return bool(reverted)


def find_tickets(event: Event, query: str):
    """Manual lookup by short code or buyer e-mail (emergency desk search)."""
    query = query.strip()
    if not query:
        return Ticket.objects.none()
    return (
        Ticket.objects.filter(event=event)
        .filter(Q(short_code__icontains=query) | Q(buyer_email__icontains=query))
        .order_by("buyer_email")
    )


def offline_manifest_tickets(event: Event) -> list[dict]:
    """
    OBS-07: data the scanner caches in the browser for offline verification.
    `token_hash` is the same SHA-256 already stored server-side (never the
    raw QR token, which the server itself doesn't retain after issuing) — the
    browser hashes a scanned token client-side and compares hashes, so no
    secret material has to leave the server.
    """
    return [
        {
            "short_code": t.short_code,
            "token_hash": t.qr_token_hash,
            "buyer_email": t.buyer_email,
            "status": t.status,
        }
        for t in Ticket.objects.filter(event=event).only(
            "short_code", "qr_token_hash", "buyer_email", "status"
        )
    ]


def active_tickets_for_door_list(event: Event):
    """Printed fallback list for the gate when the scanner is down: only
    tickets a guest could still be admitted on (sold and not yet used,
    refunded, cancelled or invalidated) — sorted by e-mail so staff can
    find the person standing in front of them by asking for it."""
    return (
        Ticket.objects.filter(event=event, status=TicketStatus.ISSUED)
        .select_related("pool")
        .order_by("buyer_email", "short_code")
    )


def write_emergency_list(event: Event, file_obj, actor=None) -> int:
    """Spec section 16: CSV with everything needed for offline entrance."""
    writer = csv.writer(file_obj)
    writer.writerow(
        ["Wydarzenie", "Data", "Kod biletu", "E-mail kupującego", "Status",
         "Zamówienie", "Wejście (zaznacz ręcznie)"]
    )
    count = 0
    tickets = (
        Ticket.objects.filter(event=event)
        .select_related("order")
        .order_by("buyer_email", "short_code")
    )
    for ticket in tickets:
        writer.writerow(
            [
                event.title,
                timezone.localtime(event.start_at).strftime("%Y-%m-%d %H:%M"),
                ticket.short_code,
                ticket.buyer_email,
                ticket.get_status_display(),
                ticket.order.short_id,
                "",
            ]
        )
        count += 1
    log_action("emergency_list.exported", event, actor=actor, metadata={"tickets": count})
    return count
