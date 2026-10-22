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


class CancellationStatus(models.TextChoices):
    """What happens with a paid order of a cancelled event (06.10.2026)."""
    NONE = "", "—"
    AWAITING = "AWAITING", "Czeka na decyzję kupującego"
    MOVED = "MOVED", "Przeniesione na nowy termin"
    REFUND_REQUESTED = "REFUND_REQUESTED", "Zwrot zlecony"
    REFUND_FAILED = "REFUND_FAILED", "Zwrot nieudany — do obsługi ręcznej"


class Order(models.Model):
    id = models.UUIDField(verbose_name="ID", primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(
        "events.Event", verbose_name="wydarzenie", on_delete=models.PROTECT, related_name="orders",
    )
    pool = models.ForeignKey(
        "events.TicketPool", verbose_name="pula", on_delete=models.PROTECT, related_name="orders"
    )
    buyer_email = models.EmailField("e-mail kupującego")
    quantity = models.PositiveSmallIntegerField("liczba biletów")
    amount_gross = models.DecimalField("kwota brutto", max_digits=9, decimal_places=2)
    currency = models.CharField(verbose_name="waluta", max_length=8, default="PLN")
    status = models.CharField(
        max_length=20, choices=OrderStatus.choices, default=OrderStatus.CREATED
    )
    payment_provider = models.CharField(
        verbose_name="operator płatności",
        max_length=10, choices=PaymentProviderKind.choices,
    )
    payment_session_id = models.CharField(
        verbose_name="ID sesji płatności",
        max_length=200, blank=True,
    )
    provider_order_id = models.CharField(
        verbose_name="ID zamówienia u operatora",
        max_length=200, blank=True,
    )
    # Provider-hosted checkout page (Stripe session URL / internal demo cash desk).
    checkout_url = models.CharField(verbose_name="adres kasy", max_length=500, blank=True)
    # Seated events: which seats the order took (keys of apps.seating.EventSeat)
    # and their labels. Kept after expiry/refund too — the seats themselves go
    # back on sale, this stays as the history ("Stolik 5 · miejsce 3").
    seat_keys = models.JSONField("miejsca (ID)", default=list, blank=True)
    seat_labels = models.JSONField("miejsca", default=list, blank=True)
    seat_prices = models.JSONField("ceny miejsc", default=list, blank=True)  # per seat_keys
    # Partial refunds (OrderRefund) keep the order PAID; these track what has
    # already gone back. A full refund sets them to amount_gross / quantity.
    refunded_amount = models.DecimalField(
        "zwrócona kwota", max_digits=9, decimal_places=2, default=0
    )
    refunded_quantity = models.PositiveSmallIntegerField("zwrócone bilety", default=0)
    is_demo = models.BooleanField("zamówienie demo", editable=False)
    expires_at = models.DateTimeField("wygasa", null=True, blank=True)
    created_at = models.DateTimeField(verbose_name="utworzono", auto_now_add=True)
    paid_at = models.DateTimeField(verbose_name="opłacono", null=True, blank=True)
    refunded_at = models.DateTimeField(verbose_name="zwrócono", null=True, blank=True)
    # Event cancelled: the buyer's choice and where the order came from if it
    # was moved to the new date (tickets keep their codes).
    cancellation_status = models.CharField(
        "odwołanie wydarzenia", max_length=20, choices=CancellationStatus.choices,
        default=CancellationStatus.NONE, blank=True,
    )
    cancellation_decided_at = models.DateTimeField("decyzja (odwołanie)", null=True, blank=True)
    moved_from_event = models.ForeignKey(
        "events.Event", verbose_name="przeniesione z wydarzenia", null=True, blank=True,
        on_delete=models.PROTECT, related_name="moved_orders",
    )
    # Terms of sale accepted at purchase (version = date of the terms text).
    terms_version = models.CharField("wersja regulaminu", max_length=20, blank=True)
    terms_accepted_at = models.DateTimeField("akceptacja regulaminu", null=True, blank=True)

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

    @property
    def seat_lines(self) -> list[tuple[str, str]]:
        """[(label, price)] for the order page and e-mails."""
        prices = list(self.seat_prices) + [""] * len(self.seat_labels)
        return list(zip(self.seat_labels, prices[: len(self.seat_labels)], strict=True))

    @property
    def active_quantity(self) -> int:
        """Tickets still held by the buyer (not refunded)."""
        return self.quantity - self.refunded_quantity

    @property
    def kept_amount(self):
        """Money the club keeps for this order after partial refunds."""
        return self.amount_gross - self.refunded_amount


class RefundStatus(models.TextChoices):
    REQUESTED = "REQUESTED", "Zlecony"
    SUCCEEDED = "SUCCEEDED", "Zwrócony"
    FAILED = "FAILED", "Nieudany"


class OrderRefund(models.Model):
    """Refund of selected tickets of a paid order (partial refund).

    Created when staff requests it; the money movement is confirmed by the
    provider's refund webhook (payload carries this record's id), which then
    invalidates the tickets and puts their seats back on sale.
    """

    order = models.ForeignKey(
        Order, verbose_name="zamówienie", on_delete=models.PROTECT, related_name="refunds"
    )
    ticket_ids = models.JSONField("bilety (ID)", default=list)
    ticket_codes = models.JSONField("kody biletów", default=list)
    seat_labels = models.JSONField("miejsca", default=list, blank=True)
    quantity = models.PositiveSmallIntegerField("liczba biletów")
    amount = models.DecimalField("kwota", max_digits=9, decimal_places=2)
    status = models.CharField(
        "status", max_length=10, choices=RefundStatus.choices, default=RefundStatus.REQUESTED
    )
    reason = models.TextField("powód", blank=True)
    provider_refund_id = models.CharField("ID zwrotu u operatora", max_length=200, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="zlecił(a)", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField("zlecono", auto_now_add=True)
    completed_at = models.DateTimeField("zakończono", null=True, blank=True)

    class Meta:
        verbose_name = "zwrot części biletów"
        verbose_name_plural = "zwroty części biletów"
        ordering = ["created_at"]

    def __str__(self):
        return f"Zwrot {self.quantity} bil. ({self.amount}) — {self.order.short_id}"


class PaymentEventStatus(models.TextChoices):
    RECEIVED = "RECEIVED", "Odebrany"
    PROCESSED = "PROCESSED", "Przetworzony"
    DUPLICATE = "DUPLICATE", "Duplikat"
    IGNORED = "IGNORED", "Zignorowany"
    ERROR = "ERROR", "Błąd"


class PaymentEvent(models.Model):
    """Log of every webhook/payment notification. Idempotency anchor."""

    provider = models.CharField(
        verbose_name="operator",
        max_length=10, choices=PaymentProviderKind.choices,
    )
    provider_event_id = models.CharField(verbose_name="ID zdarzenia u operatora", max_length=200)
    event_type = models.CharField(verbose_name="typ", max_length=100)
    order = models.ForeignKey(
        Order,
        verbose_name="zamówienie",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="payment_events",
    )
    amount = models.DecimalField(
        verbose_name="kwota",
        max_digits=9, decimal_places=2, null=True, blank=True,
    )
    processing_status = models.CharField(
        verbose_name="status",
        max_length=20, choices=PaymentEventStatus.choices, default=PaymentEventStatus.RECEIVED
    )
    payload_hash = models.CharField(verbose_name="skrót treści", max_length=64, blank=True)
    is_demo = models.BooleanField(verbose_name="demo", editable=False)
    received_at = models.DateTimeField(verbose_name="otrzymano", auto_now_add=True)
    processed_at = models.DateTimeField(verbose_name="przetworzono", null=True, blank=True)

    class Meta:
        verbose_name = "wpis dziennika płatności"
        verbose_name_plural = "dziennik płatności"
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

    mode = models.CharField(
        verbose_name="tryb",
        max_length=10, choices=PaymentMode.choices, default=PaymentMode.DEMO,
    )
    updated_at = models.DateTimeField(verbose_name="zmieniono", auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="zmienił(a)",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
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
