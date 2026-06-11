"""
E-mail delivery.

E-mails are persisted in the outbox and sent IMMEDIATELY after the
transaction that created them commits — the buyer gets the ticket e-mail
right after paying, like in any live system. The outbox + cron
(`send_emails`) is only the retry safety net for SMTP failures.
"""

import logging
from email.message import MIMEPart

from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

from .models import EmailOutbox, EmailStatus

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


def _build_message(item: EmailOutbox) -> EmailMultiAlternatives:
    from .services import qr_png_path

    message = EmailMultiAlternatives(
        subject=item.subject,
        body=item.body_text,
        to=[item.to_email],
    )
    if item.body_html:
        message.attach_alternative(item.body_html, "text/html")

    if item.order is not None:
        for ticket in item.order.tickets.all():
            path = qr_png_path(ticket)
            if not path.exists():
                continue
            image = MIMEPart()
            image.set_content(
                path.read_bytes(),
                maintype="image",
                subtype="png",
                cid=f"<qr-{ticket.id}>",
                disposition="inline",
                filename=f"bilet-{ticket.short_code}.png",
            )
            message.attach(image)
    return message


def _send_item(item: EmailOutbox) -> bool:
    """One delivery attempt; on failure the item stays PENDING for the cron."""
    item.attempts += 1
    try:
        _build_message(item).send()
    except Exception as exc:  # noqa: BLE001 - any backend failure must not stop the queue
        logger.warning("E-mail %s failed (attempt %s): %s", item.pk, item.attempts, exc)
        item.last_error = str(exc)[:2000]
        if item.attempts >= MAX_ATTEMPTS:
            item.status = EmailStatus.FAILED
        item.save(update_fields=["attempts", "last_error", "status"])
        return False
    item.status = EmailStatus.SENT
    item.sent_at = timezone.now()
    item.last_error = ""
    item.save(update_fields=["attempts", "status", "sent_at", "last_error"])
    return True


def send_now(item_pk: int) -> bool:
    """Immediate delivery, called via transaction.on_commit after queueing."""
    item = EmailOutbox.objects.filter(
        pk=item_pk, status=EmailStatus.PENDING, attempts__lt=MAX_ATTEMPTS
    ).first()
    if item is None:
        return False
    return _send_item(item)


def send_pending_emails(limit: int = 50) -> int:
    """Retry path (cron): send everything still waiting in the outbox."""
    pending = EmailOutbox.objects.filter(
        status=EmailStatus.PENDING, attempts__lt=MAX_ATTEMPTS
    ).order_by("created_at")[:limit]

    sent = 0
    for item in pending:
        if _send_item(item):
            sent += 1
    return sent
