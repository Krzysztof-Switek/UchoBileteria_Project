import uuid

from django.conf import settings
from django.db import models

from apps.statemachine import assert_transition


class TicketStatus(models.TextChoices):
    ISSUED = "ISSUED", "Wydany"
    CHECKED_IN = "CHECKED_IN", "Wykorzystany (wejście)"
    REFUNDED = "REFUNDED", "Zwrócony"
    CANCELLED = "CANCELLED", "Anulowany"
    INVALIDATED = "INVALIDATED", "Unieważniony"


TICKET_TRANSITIONS = {
    TicketStatus.ISSUED: {
        TicketStatus.CHECKED_IN,
        TicketStatus.REFUNDED,
        TicketStatus.CANCELLED,
        TicketStatus.INVALIDATED,
    },
    # Admin correction after offline check-in reconciliation, and OBS-06's
    # door-staff "cofnij wpuszczenie" for a scan made by mistake:
    TicketStatus.CHECKED_IN: {TicketStatus.INVALIDATED, TicketStatus.ISSUED},
    TicketStatus.REFUNDED: set(),
    TicketStatus.CANCELLED: set(),
    TicketStatus.INVALIDATED: set(),
}


class Ticket(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="tickets")
    pool = models.ForeignKey(
        "events.TicketPool", on_delete=models.PROTECT, related_name="tickets"
    )
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="tickets")
    buyer_email = models.EmailField()
    short_code = models.CharField("kod biletu", max_length=12, unique=True)
    qr_token_hash = models.CharField(max_length=64, unique=True, editable=False)
    status = models.CharField(
        max_length=20, choices=TicketStatus.choices, default=TicketStatus.ISSUED
    )
    is_demo = models.BooleanField(editable=False)
    issued_at = models.DateTimeField(auto_now_add=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tickets_checked_in",
    )
    refunded_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "bilet"
        verbose_name_plural = "bilety"
        ordering = ["-issued_at"]
        indexes = [
            models.Index(fields=["event", "status"]),
            models.Index(fields=["buyer_email"]),
        ]
        permissions = [
            ("checkin_ticket", "Może odprawiać bilety przy wejściu"),
        ]

    def __str__(self):
        return f"Bilet {self.short_code} ({self.get_status_display()})"

    def transition_to(self, new_status: str) -> None:
        """Validate and apply a status change (does not save)."""
        assert_transition("Ticket", TICKET_TRANSITIONS, self.status, new_status)
        self.status = new_status


class EmailStatus(models.TextChoices):
    PENDING = "PENDING", "Oczekuje"
    SENT = "SENT", "Wysłany"
    FAILED = "FAILED", "Błąd"


class EmailOutbox(models.Model):
    """Outbox queue: e-mails are persisted first, then sent with retries by cron."""

    to_email = models.EmailField()
    subject = models.CharField(max_length=300)
    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)
    order = models.ForeignKey(
        "orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="emails"
    )
    status = models.CharField(
        max_length=10, choices=EmailStatus.choices, default=EmailStatus.PENDING
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "e-mail (kolejka)"
        verbose_name_plural = "e-maile (kolejka)"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status"])]

    def __str__(self):
        return f"{self.to_email}: {self.subject} [{self.status}]"
