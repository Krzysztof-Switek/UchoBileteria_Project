"""
Ticket issuing (after confirmed payment), QR generation and e-mail queueing.

Security: the raw QR token exists only at issue time. The database stores its
SHA-256 hash; the token itself is baked into the QR PNG saved on disk and is
never stored as text.
"""

import hashlib
import secrets
from pathlib import Path

import qrcode
from django.conf import settings
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from apps.auditlog.services import log_action
from apps.orders.models import Order, OrderStatus

from .models import EmailOutbox, Ticket, TicketStatus

# Unambiguous characters only (no 0/O, 1/I/L) — staff read these aloud.
SHORT_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _generate_short_code() -> str:
    head = "".join(secrets.choice(SHORT_CODE_ALPHABET) for _ in range(4))
    tail = "".join(secrets.choice("23456789") for _ in range(2))
    return f"{head}-{tail}"


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def verify_url(raw_token: str) -> str:
    return f"{settings.SITE_BASE_URL}/wejscie/weryfikuj?t={raw_token}"


def qr_png_path(ticket: Ticket) -> Path:
    return Path(settings.MEDIA_ROOT) / "qr" / f"{ticket.id}.png"


def _write_qr_png(ticket: Ticket, raw_token: str) -> None:
    path = qr_png_path(ticket)
    path.parent.mkdir(parents=True, exist_ok=True)
    image = qrcode.make(verify_url(raw_token))
    image.save(path)


def issue_tickets_for_order(order: Order) -> list[Ticket]:
    """
    Create one ticket per purchased seat. Idempotent: if the order already
    has tickets (duplicate webhook), nothing new is created.
    """
    if order.status != OrderStatus.PAID:
        return []

    with transaction.atomic():
        existing = list(order.tickets.all())
        if existing:
            return existing

        tickets: list[tuple[Ticket, str]] = []
        for _ in range(order.quantity):
            raw_token = secrets.token_urlsafe(32)
            ticket = None
            for _attempt in range(20):
                try:
                    with transaction.atomic():
                        ticket = Ticket.objects.create(
                            event=order.event,
                            pool=order.pool,
                            order=order,
                            buyer_email=order.buyer_email,
                            short_code=_generate_short_code(),
                            qr_token_hash=hash_token(raw_token),
                            status=TicketStatus.ISSUED,
                            is_demo=order.is_demo,
                        )
                    break
                except IntegrityError:  # short-code collision — try another
                    continue
            if ticket is None:
                raise RuntimeError("Nie udało się wygenerować unikalnego kodu biletu.")
            tickets.append((ticket, raw_token))

        for ticket, raw_token in tickets:
            _write_qr_png(ticket, raw_token)

        queue_ticket_email(order, [t for t, _ in tickets])

    log_action(
        "tickets.issued",
        order,
        metadata={"count": len(tickets), "codes": [t.short_code for t, _ in tickets]},
    )
    return [t for t, _ in tickets]


def generate_tickets_pdf(order: Order) -> bytes:
    """KUP-03: one A5 page per ticket — event, date, venue, pool, QR and
    short code — so a ticket is self-sufficient without the site around it."""
    from io import BytesIO

    from reportlab.lib.pagesizes import A5
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A5)
    width, height = A5

    for ticket in order.tickets.all():
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(15 * mm, height - 20 * mm, ticket.event.title)

        pdf.setFont("Helvetica", 11)
        pdf.drawString(
            15 * mm, height - 28 * mm, ticket.event.start_at.strftime("%d.%m.%Y %H:%M")
        )
        pdf.drawString(15 * mm, height - 34 * mm, ticket.event.venue_name)
        pdf.drawString(15 * mm, height - 40 * mm, ticket.pool.name)

        qr_path = qr_png_path(ticket)
        if qr_path.exists():
            pdf.drawImage(str(qr_path), 15 * mm, height - 100 * mm, width=60 * mm, height=60 * mm)

        pdf.setFont("Helvetica-Bold", 14)
        pdf.drawString(15 * mm, height - 108 * mm, ticket.short_code)
        pdf.setFont("Helvetica", 9)
        pdf.drawString(15 * mm, height - 114 * mm, ticket.get_status_display())

        pdf.showPage()

    pdf.save()
    return buffer.getvalue()


def invalidate_tickets_for_refunded_order(order: Order) -> int:
    """Mark all still-valid tickets of a refunded order as REFUNDED."""
    now = timezone.now()
    count = 0
    for ticket in order.tickets.filter(status=TicketStatus.ISSUED):
        ticket.transition_to(TicketStatus.REFUNDED)
        ticket.refunded_at = now
        ticket.save(update_fields=["status", "refunded_at"])
        count += 1
    if count:
        log_action("tickets.invalidated_after_refund", order, metadata={"count": count})
        queue_refund_email(order)
    return count


# --- E-mail queue ----------------------------------------------------------


def _queue_email(order: Order, subject: str, template: str, context: dict) -> EmailOutbox:
    """
    Persist the e-mail, then deliver it the moment the surrounding
    transaction commits — buyers get their e-mail immediately, while the
    outbox row guarantees a cron retry if SMTP happens to be down.
    """
    item = EmailOutbox.objects.create(
        to_email=order.buyer_email,
        subject=subject,
        body_text=render_to_string(f"emails/{template}.txt", context),
        body_html=render_to_string(f"emails/{template}.html", context),
        order=order,
    )

    from .sending import send_now

    transaction.on_commit(lambda: send_now(item.pk))
    return item


def queue_ticket_email(order: Order, tickets: list[Ticket]) -> EmailOutbox:
    context = {"order": order, "tickets": tickets, "event": order.event}
    return _queue_email(order, f"Twoje bilety: {order.event.title}", "tickets", context)


def queue_refund_email(order: Order) -> EmailOutbox:
    context = {"order": order, "event": order.event}
    return _queue_email(order, f"Zwrot za bilety: {order.event.title}", "refund", context)
