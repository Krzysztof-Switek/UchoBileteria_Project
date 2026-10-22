"""
Seat planner: the room (fixed), reusable layouts (furniture) and the per-event
seat map (frozen copy + one row per sellable seat).

Three layers, see docs/Hendouts TODOs/03.10_planer_miejsc_PLAN.md §1:
- Room — the real hall imported from the technical model (sala_glowna.json),
- SeatingLayout — tables/rows/chairs placed on a room, saved for reuse,
- EventSeatMap + EventSeat — a snapshot attached to one event; seats are the
  rows that get reserved and sold, so editing a layout never changes an event.

Coordinates everywhere are centimetres in the room's canonical plan: u to the
right and v downwards, with the stage at the top (v = 0 at the back wall of
the stage). Numbers travel as JSON, never through {{ }} (Polish locale comma).
"""

from django.conf import settings
from django.db import models


class Room(models.Model):
    name = models.CharField("nazwa", max_length=200)
    slug = models.SlugField("identyfikator", unique=True)
    width_cm = models.PositiveIntegerField("szerokość (cm)")
    depth_cm = models.PositiveIntegerField("głębokość (cm)")
    # Canonical-plan geometry: outline, obstacles (stage, bar, columns…),
    # entrances and context drawn under the furniture. Built by
    # `manage.py import_hall` from the technical model — not edited by hand.
    geometry = models.JSONField("geometria sali", default=dict)
    # geometry = base_geometry (from the technical model) + overrides (owner's
    # clicks in "Ustawienia sali": hidden elements, hand-drawn ones) — apps/seating/room.py
    base_geometry = models.JSONField("geometria z modelu technicznego", default=dict)
    overrides = models.JSONField("zmiany właściciela", default=dict, blank=True)
    source = models.CharField("źródło danych", max_length=300, blank=True)
    created_at = models.DateTimeField("utworzono", auto_now_add=True)
    updated_at = models.DateTimeField("zmieniono", auto_now=True)

    class Meta:
        verbose_name = "sala"
        verbose_name_plural = "sale"
        ordering = ["name"]

    def __str__(self):
        return self.name


class SeatingLayout(models.Model):
    room = models.ForeignKey(
        Room, verbose_name="sala", on_delete=models.PROTECT, related_name="layouts"
    )
    name = models.CharField("nazwa układu", max_length=200)
    description = models.TextField("opis", blank=True)
    data = models.JSONField("układ (JSON)", default=dict)
    schema_version = models.PositiveSmallIntegerField("wersja schematu", default=1)
    seat_count = models.PositiveIntegerField("liczba miejsc", default=0)
    table_count = models.PositiveIntegerField("liczba stolików/rzędów", default=0)
    based_on = models.ForeignKey(
        "self", verbose_name="skopiowany z", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="copies",
    )
    is_archived = models.BooleanField("zarchiwizowany", default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="utworzył(a)", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="layouts_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="zmienił(a)", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="layouts_updated",
    )
    created_at = models.DateTimeField("utworzono", auto_now_add=True)
    updated_at = models.DateTimeField("zmieniono", auto_now=True)

    class Meta:
        verbose_name = "układ sali"
        verbose_name_plural = "układy sali"
        ordering = ["is_archived", "name"]

    def __str__(self):
        return self.name


class EventSeatMap(models.Model):
    """Frozen copy of a layout attached to one event (warstwa 3)."""

    event = models.OneToOneField(
        "events.Event", verbose_name="wydarzenie", on_delete=models.CASCADE,
        related_name="seat_map",
    )
    source_layout = models.ForeignKey(
        SeatingLayout, verbose_name="układ źródłowy", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="event_maps",
    )
    source_layout_name = models.CharField("nazwa układu", max_length=200)
    room_snapshot = models.JSONField("sala (kopia)", default=dict)
    layout_snapshot = models.JSONField("układ (kopia)", default=dict)
    created_at = models.DateTimeField("przypięto", auto_now_add=True)

    class Meta:
        verbose_name = "mapa miejsc wydarzenia"
        verbose_name_plural = "mapy miejsc wydarzeń"

    def __str__(self):
        return f"{self.event} — {self.source_layout_name}"


class SeatStatus(models.TextChoices):
    AVAILABLE = "AVAILABLE", "Wolne"
    HELD = "HELD", "Zarezerwowane (czeka na płatność)"
    SOLD = "SOLD", "Sprzedane"
    BLOCKED = "BLOCKED", "Zablokowane"


class SellMode(models.TextChoices):
    SEATS = "SEATS", "Pojedyncze miejsca"
    TABLE = "TABLE", "Tylko cały stolik"
    BOTH = "BOTH", "Miejsca albo cały stolik"


class GroupKind(models.TextChoices):
    TABLE = "TABLE", "Stolik"
    ROW = "ROW", "Rząd"


class EventSeat(models.Model):
    event = models.ForeignKey(
        "events.Event", verbose_name="wydarzenie", on_delete=models.CASCADE,
        related_name="seats",
    )
    seat_key = models.CharField("ID miejsca", max_length=40)
    group_key = models.CharField("ID stolika/rzędu", max_length=40)
    group_kind = models.CharField(
        "rodzaj", max_length=10, choices=GroupKind.choices, default=GroupKind.TABLE
    )
    group_label = models.CharField("stolik/rząd", max_length=20)
    seat_label = models.CharField("miejsce", max_length=20)
    sell_mode = models.CharField(
        "tryb sprzedaży", max_length=10, choices=SellMode.choices, default=SellMode.SEATS
    )
    category = models.CharField("strefa cenowa", max_length=20, blank=True)
    # % of the active pool's price (layout price zones, frozen at attach time)
    price_percent = models.PositiveSmallIntegerField("cena (% ceny puli)", default=100)
    sort_index = models.PositiveIntegerField("kolejność", default=0)
    status = models.CharField(
        "status", max_length=10, choices=SeatStatus.choices, default=SeatStatus.AVAILABLE
    )
    order = models.ForeignKey(
        "orders.Order", verbose_name="zamówienie", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="seats",
    )
    block_note = models.CharField("notatka blokady", max_length=200, blank=True)
    updated_at = models.DateTimeField("zmieniono", auto_now=True)

    class Meta:
        verbose_name = "miejsce na wydarzeniu"
        verbose_name_plural = "miejsca na wydarzeniach"
        ordering = ["event", "sort_index"]
        constraints = [
            models.UniqueConstraint(fields=["event", "seat_key"], name="event_seat_unique_key"),
            # A held/sold seat always belongs to an order; a free one never does.
            models.CheckConstraint(
                condition=(
                    models.Q(status__in=["HELD", "SOLD"], order__isnull=False)
                    | models.Q(status__in=["AVAILABLE", "BLOCKED"], order__isnull=True)
                ),
                name="event_seat_order_matches_status",
            ),
        ]
        indexes = [models.Index(fields=["event", "status"])]

    def __str__(self):
        return self.label

    @property
    def label(self) -> str:
        return format_seat_label(self.group_kind, self.group_label, self.seat_label)


def format_seat_label(group_kind: str, group_label: str, seat: str) -> str:
    """Human label used on tickets, e-mails, the scanner and door lists."""
    group = "Rząd" if group_kind == GroupKind.ROW else "Stolik"
    return f"{group} {group_label} · miejsce {seat}"
