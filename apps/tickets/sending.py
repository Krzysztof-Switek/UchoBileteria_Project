"""Outbox sender: attaches QR codes and delivers queued e-mails with retries."""

import logging
from email.message import MIMEPart

from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

from .models import EmailOutbox, EmailStatus
from .services import qr_png_path

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


def _build_message(item: EmailOutbox) -> EmailMultiAlternatives:
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


def send_pending_emails(limit: int = 50) -> int:
    """Send queued e-mails; failures stay PENDING until MAX_ATTEMPTS."""
    pending = EmailOutbox.objects.filter(
        status=EmailStatus.PENDING, attempts__lt=MAX_ATTEMPTS
    ).order_by("created_at")[:limit]

    sent = 0
    for item in pending:
        item.attempts += 1
        try:
            _build_message(item).send()
        except Exception as exc:  # noqa: BLE001 - any backend failure must not stop the queue
            logger.warning("E-mail %s failed (attempt %s): %s", item.pk, item.attempts, exc)
            item.last_error = str(exc)[:2000]
            if item.attempts >= MAX_ATTEMPTS:
                item.status = EmailStatus.FAILED
            item.save(update_fields=["attempts", "last_error", "status"])
            continue
        item.status = EmailStatus.SENT
        item.sent_at = timezone.now()
        item.last_error = ""
        item.save(update_fields=["attempts", "status", "sent_at", "last_error"])
        sent += 1
    return sent
