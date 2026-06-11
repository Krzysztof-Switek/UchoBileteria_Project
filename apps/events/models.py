from django.conf import settings
from django.db import models
from django.utils import timezone


class EventStatus(models.TextChoices):
    """Stored statuses. SOLD_OUT and SALES_CLOSED are computed at read time."""

    DRAFT = "DRAFT", "Szkic"
    PUBLISHED = "PUBLISHED", "Opublikowane"
    CANCELLED = "CANCELLED", "Odwołane"
    FINISHED = "FINISHED", "Zakończone"


class EffectiveEventStatus:
    """Computed sales statuses, superset of EventStatus values."""

    DRAFT = EventStatus.DRAFT
    PUBLISHED = EventStatus.PUBLISHED
    CANCELLED = EventStatus.CANCELLED
    FINISHED = EventStatus.FINISHED
    SOLD_OUT = "SOLD_OUT"
    SALES_CLOSED = "SALES_CLOSED"


class Event(models.Model):
    title = models.CharField("tytuł", max_length=200)
    slug = models.SlugField(unique=True)
    description = models.TextField("opis", blank=True)
    venue_name = models.CharField("nazwa miejsca", max_length=200, default="Klub UCHO")
    venue_address = models.CharField("adres", max_length=300, blank=True)
    start_at = models.DateTimeField("początek")
    end_at = models.DateTimeField("koniec")
    sales_start_at = models.DateTimeField("start sprzedaży")
    sales_end_at = models.DateTimeField("koniec sprzedaży")
    capacity_total = models.PositiveIntegerField("pojemność")
    sold_total = models.PositiveIntegerField("sprzedane", default=0)
    status = models.CharField(
        max_length=20, choices=EventStatus.choices, default=EventStatus.DRAFT
    )
    max_tickets_per_order = models.PositiveSmallIntegerField(default=10)
    calendar_event_id = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events_updated",
    )

    class Meta:
        verbose_name = "wydarzenie"
        verbose_name_plural = "wydarzenia"
        ordering = ["start_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(sold_total__lte=models.F("capacity_total")),
                name="event_sold_within_capacity",
            ),
        ]

    def __str__(self):
        return self.title

    @property
    def is_sold_out(self) -> bool:
        return self.sold_total >= self.capacity_total

    @property
    def tickets_left(self) -> int:
        return self.capacity_total - self.sold_total

    def effective_status(self, now=None) -> str:
        """Sales status computed from stored status, dates and counters."""
        now = now or timezone.now()
        if self.status != EventStatus.PUBLISHED:
            return self.status
        if self.is_sold_out:
            return EffectiveEventStatus.SOLD_OUT
        if now > self.sales_end_at:
            return EffectiveEventStatus.SALES_CLOSED
        return EventStatus.PUBLISHED


class PoolManualStatus(models.TextChoices):
    AUTO = "AUTO", "Automatyczna"
    FORCED_OPEN = "FORCED_OPEN", "Wymuszona otwarta"
    FORCED_CLOSED = "FORCED_CLOSED", "Wymuszona zamknięta"


class PoolStatus:
    """Computed pool statuses (not stored)."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SOLD_OUT = "SOLD_OUT"
    CLOSED = "CLOSED"


class TicketPool(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="pools")
    name = models.CharField("nazwa", max_length=100)
    priority = models.PositiveSmallIntegerField("priorytet")
    price_gross = models.DecimalField("cena brutto", max_digits=8, decimal_places=2)
    currency = models.CharField(max_length=8, default="PLN")
    capacity = models.PositiveIntegerField("pojemność puli")
    sold_count = models.PositiveIntegerField("sprzedane", default=0)
    sales_start_at = models.DateTimeField("start sprzedaży puli", null=True, blank=True)
    sales_end_at = models.DateTimeField("koniec sprzedaży puli", null=True, blank=True)
    manual_status = models.CharField(
        max_length=20, choices=PoolManualStatus.choices, default=PoolManualStatus.AUTO
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "pula biletów"
        verbose_name_plural = "pule biletów"
        ordering = ["event", "priority"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(sold_count__lte=models.F("capacity")),
                name="pool_sold_within_capacity",
            ),
            models.UniqueConstraint(
                fields=["event", "priority"], name="pool_priority_unique_per_event"
            ),
        ]

    def __str__(self):
        return f"{self.event} – {self.name}"

    @property
    def is_sold_out(self) -> bool:
        return self.sold_count >= self.capacity

    @property
    def tickets_left(self) -> int:
        return self.capacity - self.sold_count
