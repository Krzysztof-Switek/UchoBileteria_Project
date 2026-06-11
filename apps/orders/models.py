import uuid

from django.conf import settings
from django.db import models

from apps.statemachine import assert_transition


class PaymentProviderKind(models.TextChoices):
    DEMO = "DEMO", "Demo (waluta wirtualna)"
    STRIPE = "STRIPE", "Stripe"


class OrderStatus(models.TextChoices):
    CREATED = "CREATED", "Utworzone"
    PAYMENT_PENDING = "PAYMENT_PENDING", "Oczekuje na płatność"
    PAID = "PAID", "Opłacone"
    FAILED = "FAILED", "Nieudane"
    EXPIRED = "EXPIRED", "Wygasłe"
    REFUNDED = "REFUNDED", "Zwrócone"
    CANCELLED = "CANCELLED", "Anulowane"


ORDER_TRANSITIONS = {
    OrderStatus.CREATED: {
        OrderStatus.PAYMENT_PENDING,
        OrderStatus.EXPIRED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.PAYMENT_PENDING: {
        OrderStatus.PAID,
        OrderStatus.FAILED,
        OrderStatus.EXPIRED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.PAID: {OrderStatus.REFUNDED},
    OrderStatus.FAILED: {OrderStatus.PAYMENT_PENDING},  # retry payment
    OrderStatus.EXPIRED: {OrderStatus.PAID},  # late webhook, capacity re-checked
    OrderStatus.REFUNDED: set(),
    OrderStatus.CANCELLED: set(),
}


class Order(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="orders")
    pool = models.ForeignKey(
        "events.TicketPool", on_delete=models.PROTECT, related_name="orders"
    )
    buyer_email = models.EmailField("e-mail kupującego")
    quantity = models.PositiveSmallIntegerField("liczba biletów")
    amount_gross = models.DecimalField("kwota brutto", max_digits=9, decimal_places=2)
    currency = models.CharField(max_length=8, default="PLN")
    status = models.CharField(
        max_length=20, choices=OrderStatus.choices, default=OrderStatus.CREATED
    )
    payment_provider = models.CharField(max_length=10, choices=PaymentProviderKind.choices)
    payment_session_id = models.CharField(max_length=200, blank=True)
    provider_order_id = models.CharField(max_length=200, blank=True)
    # Provider-hosted checkout page (Stripe session URL / internal demo cash desk).
    checkout_url = models.CharField(max_length=500, blank=True)
    is_demo = models.BooleanField("zamówienie demo", editable=False)
    expires_at = models.DateTimeField("wygasa", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "zamówienie"
        verbose_name_plural = "zamówienia"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "expires_at"]),
            models.Index(fields=["buyer_email"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1), name="order_quantity_positive"
            ),
        ]

    def __str__(self):
        return f"Zamówienie {self.short_id} ({self.buyer_email})"

    @property
    def short_id(self) -> str:
        return str(self.id)[:8]

    def transition_to(self, new_status: str) -> None:
        """Validate and apply a status change (does not save)."""
        assert_transition("Order", ORDER_TRANSITIONS, self.status, new_status)
        self.status = new_status


class PaymentEventStatus(models.TextChoices):
    RECEIVED = "RECEIVED", "Odebrany"
    PROCESSED = "PROCESSED", "Przetworzony"
    DUPLICATE = "DUPLICATE", "Duplikat"
    IGNORED = "IGNORED", "Zignorowany"
    ERROR = "ERROR", "Błąd"


class PaymentEvent(models.Model):
    """Log of every webhook/payment notification. Idempotency anchor."""

    provider = models.CharField(max_length=10, choices=PaymentProviderKind.choices)
    provider_event_id = models.CharField(max_length=200)
    event_type = models.CharField(max_length=100)
    order = models.ForeignKey(
        Order, null=True, blank=True, on_delete=models.SET_NULL, related_name="payment_events"
    )
    amount = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    processing_status = models.CharField(
        max_length=20, choices=PaymentEventStatus.choices, default=PaymentEventStatus.RECEIVED
    )
    payload_hash = models.CharField(max_length=64, blank=True)
    is_demo = models.BooleanField(editable=False)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "zdarzenie płatności"
        verbose_name_plural = "zdarzenia płatności"
        ordering = ["-received_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_event_id"],
                name="payment_event_unique_per_provider",
            ),
        ]

    def __str__(self):
        return f"{self.provider}:{self.provider_event_id} ({self.event_type})"


class PaymentMode(models.TextChoices):
    DEMO = "DEMO", "Tryb demo"
    LIVE = "LIVE", "Tryb produkcyjny"


class PaymentConfig(models.Model):
    """DB singleton holding the global demo/live payment switch."""

    mode = models.CharField(max_length=10, choices=PaymentMode.choices, default=PaymentMode.DEMO)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        verbose_name = "konfiguracja płatności"
        verbose_name_plural = "konfiguracja płatności"

    def __str__(self):
        return f"Płatności: {self.get_mode_display()}"

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("PaymentConfig cannot be deleted.", {self})

    @classmethod
    def load(cls) -> PaymentConfig:
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @classmethod
    def is_demo_mode(cls) -> bool:
        return cls.load().mode == PaymentMode.DEMO
