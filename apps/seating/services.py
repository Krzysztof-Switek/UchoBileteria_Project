"""
Seat planner operations: saving layouts, attaching a layout to an event,
staff blocks and seat moves, availability for the buyer's map.

Reservation itself (taking seats with an order, releasing them on expiry,
failure or refund) lives next to the capacity counters in
apps/orders/services.py and apps/orders/payments.py — it must happen in the
same transaction as the counters.
"""

from collections import defaultdict

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction

from apps.auditlog.services import log_action
from apps.events.models import Event, EventStatus

from .layout import clean_layout, expand_seats, layout_counts, validate_layout
from .models import EventSeat, EventSeatMap, Room, SeatingLayout, SeatStatus, SellMode

# Hard cap on seats in one order, also for whole tables (D3: a whole table may
# exceed max_tickets_per_order, but not without any limit).
MAX_SEATS_PER_ORDER = 40


# --- layouts -------------------------------------------------------------------


def save_layout(layout: SeatingLayout, data, actor=None, *, name=None, description=None):
    """Validate and store the editor document. Returns the warnings list."""
    cleaned = clean_layout(data)  # raises LayoutError
    _, warnings = validate_layout(cleaned, layout.room.geometry)
    layout.data = cleaned
    layout.seat_count, layout.table_count = layout_counts(cleaned)
    if name is not None:
        layout.name = name
    if description is not None:
        layout.description = description
    layout.updated_by = actor
    if layout.pk is None:
        layout.created_by = actor
    layout.save()
    log_action("seating.layout_saved", layout, actor=actor,
               metadata={"seats": layout.seat_count, "warnings": len(warnings)})
    return warnings


def copy_layout(source: SeatingLayout, name: str, actor=None, data=None) -> SeatingLayout:
    """'Zapisz jako nowy układ' / 'Duplikuj' — the original stays untouched."""
    layout = SeatingLayout(room=source.room, name=name, description=source.description,
                           based_on=source)
    save_layout(layout, data if data is not None else source.data, actor)
    log_action("seating.layout_copied", layout, actor=actor,
               metadata={"source_id": source.pk, "source_name": source.name})
    return layout


def layout_usage(layout: SeatingLayout) -> list[Event]:
    return [m.event for m in layout.event_maps.select_related("event").order_by("-created_at")]


# --- event seat map --------------------------------------------------------------


def structure_locked_reason(event: Event) -> str:
    """Why the seat map of this event can no longer be replaced ('' = it can)."""
    if event.status in (EventStatus.CANCELLED, EventStatus.FINISHED):
        return "Wydarzenie jest odwołane lub zakończone."
    if event.sold_total > 0 or event.seats.filter(
        status__in=[SeatStatus.HELD, SeatStatus.SOLD]
    ).exists():
        return ("Na to wydarzenie są już sprzedane lub zarezerwowane miejsca — układu nie "
                "można zmienić. Działają blokady i przenoszenie miejsc.")
    return ""


def attach_layout(event: Event, layout: SeatingLayout, actor=None) -> list[str]:
    """
    Freeze a copy of the layout onto the event and create its seats. Allowed only
    before the first sale. Returns notes for the manager (e.g. pools too small).
    """
    reason = structure_locked_reason(event)
    if reason:
        raise ValidationError(reason)
    seats = expand_seats(layout.data)
    if not seats:
        raise ValidationError("Układ nie ma żadnych miejsc do sprzedaży.")
    if len(seats) > settings.CLUB_MAX_CAPACITY:
        raise ValidationError(
            f"Układ ma {len(seats)} miejsc — więcej niż pojemność klubu "
            f"({settings.CLUB_MAX_CAPACITY})."
        )

    caller_event = event
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=event.pk)
        if event.sold_total > 0:
            raise ValidationError(structure_locked_reason(event))
        EventSeat.objects.filter(event=event).delete()
        EventSeatMap.objects.filter(event=event).delete()
        EventSeatMap.objects.create(
            event=event,
            source_layout=layout,
            source_layout_name=layout.name,
            room_snapshot={"name": layout.room.name, "width_cm": layout.room.width_cm,
                           "depth_cm": layout.room.depth_cm, **layout.room.geometry},
            layout_snapshot=layout.data,
        )
        EventSeat.objects.bulk_create([EventSeat(event=event, **seat) for seat in seats])
        event.seating_mode = "SEATED"
        event.capacity_total = len(seats)
        event.save(update_fields=["seating_mode", "capacity_total", "updated_at"])
    # keep the caller's instance in step, so a later event.save() can't undo this
    caller_event.seating_mode, caller_event.capacity_total = "SEATED", len(seats)

    log_action("seating.map_attached", event, actor=actor,
               metadata={"layout_id": layout.pk, "layout": layout.name, "seats": len(seats)})
    notes = []
    pools_total = sum(p.capacity for p in event.pools.all())
    if pools_total < len(seats):
        notes.append(
            f"Suma biletów w pulach ({pools_total}) jest mniejsza niż liczba miejsc "
            f"({len(seats)}) — część miejsc nie zostanie sprzedana, dopóki nie zwiększysz pul."
        )
    return notes


def upcoming_maps(layout: SeatingLayout):
    """Seat maps of events that have not happened yet and use this layout — a
    change of the layout is carried over to them (user decision 06.10.2026)."""
    from django.utils import timezone

    return (
        EventSeatMap.objects.filter(source_layout=layout, event__start_at__gte=timezone.now())
        .exclude(event__status__in=[EventStatus.CANCELLED, EventStatus.FINISHED])
        .select_related("event")
        .order_by("event__start_at")
    )


def _sync_plan(event: Event, new_seats: list[dict]) -> dict:
    """What carrying the new layout over to this event would do. Seats are
    matched by their stable ID; a sold or reserved seat that the new layout no
    longer has blocks the update for this event (nothing is changed there)."""
    new_keys = {s["seat_key"] for s in new_seats}
    current = {s.seat_key: s for s in EventSeat.objects.filter(event=event)}
    removed = [s for key, s in current.items() if key not in new_keys]
    conflicts = sorted(
        (s for s in removed if s.status in (SeatStatus.HELD, SeatStatus.SOLD)),
        key=lambda s: s.sort_index,
    )
    return {
        "added": len(new_keys - current.keys()),
        "removed": len(removed),
        "blocked_removed": sum(1 for s in removed if s.status == SeatStatus.BLOCKED),
        "conflicts": [s.label for s in conflicts],
        "sold": sum(1 for s in current.values() if s.status in (SeatStatus.HELD,
                                                                  SeatStatus.SOLD)),
    }


def propagation_preview(layout: SeatingLayout, data) -> list[dict]:
    """For the confirmation dialog: which upcoming events the save will change
    and how. `data` = the layout document about to be saved."""
    new_seats = expand_seats(clean_layout(data))
    result = []
    for seat_map in upcoming_maps(layout):
        event = seat_map.event
        plan = _sync_plan(event, new_seats)
        result.append({
            "id": event.pk,
            "title": event.title,
            "start": event.start_at.isoformat(),
            "seats_before": EventSeat.objects.filter(event=event).count(),
            "seats_after": len(new_seats),
            **plan,
        })
    return result


def sync_event_map(event: Event, layout: SeatingLayout, actor=None) -> dict:
    """Carry the (saved) layout over to an upcoming event's seat map: new seats
    appear free, removed free/blocked seats disappear, kept seats keep their
    status, order and block note (labels, sale mode and price zone follow the
    layout; already sold tickets keep their price). Refused for the whole event
    if a sold or reserved seat would disappear."""
    new_seats = expand_seats(layout.data)
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=event.pk)
        plan = _sync_plan(event, new_seats)
        if plan["conflicts"]:
            return {**plan, "applied": False}
        current = {s.seat_key: s for s in EventSeat.objects.filter(event=event)}
        new_keys = {s["seat_key"] for s in new_seats}
        EventSeat.objects.filter(
            event=event, seat_key__in=[k for k in current if k not in new_keys]
        ).delete()
        fields = ["group_key", "group_kind", "group_label", "seat_label", "sell_mode",
                  "category", "price_percent", "sort_index"]
        to_update, to_create = [], []
        for seat in new_seats:
            row = current.get(seat["seat_key"])
            if row is None:
                to_create.append(EventSeat(event=event, **seat))
            else:
                for field in fields:
                    setattr(row, field, seat[field])
                to_update.append(row)
        EventSeat.objects.bulk_update(to_update, fields)
        EventSeat.objects.bulk_create(to_create)
        EventSeatMap.objects.filter(event=event).update(
            source_layout=layout,
            source_layout_name=layout.name,
            room_snapshot={"name": layout.room.name, "width_cm": layout.room.width_cm,
                           "depth_cm": layout.room.depth_cm, **layout.room.geometry},
            layout_snapshot=layout.data,
        )
        # capacity = seats that can be sold (blocked ones excluded, as in block_seats)
        blocked = EventSeat.objects.filter(event=event, status=SeatStatus.BLOCKED).count()
        event.capacity_total = len(new_seats) - blocked
        event.save(update_fields=["capacity_total", "updated_at"])
    log_action("seating.map_synced", event, actor=actor,
               metadata={"layout_id": layout.pk, "added": plan["added"],
                         "removed": plan["removed"], "seats": len(new_seats)})
    return {**plan, "applied": True}


def detach_layout(event: Event, actor=None) -> None:
    """Back to tickets without seats (only before the first sale)."""
    reason = structure_locked_reason(event)
    if reason:
        raise ValidationError(reason)
    with transaction.atomic():
        EventSeat.objects.filter(event=event).delete()
        EventSeatMap.objects.filter(event=event).delete()
        event.seating_mode = "GENERAL"
        pools_total = sum(p.capacity for p in event.pools.all())
        event.capacity_total = max(pools_total, event.sold_total, 1)
        event.save(update_fields=["seating_mode", "capacity_total", "updated_at"])
    log_action("seating.map_detached", event, actor=actor)


def block_seats(event: Event, seat_keys: list[str], note: str, actor=None) -> int:
    """Take free seats out of sale (guests, technics, wheelchair space)."""
    note = (note or "").strip()[:200]
    with transaction.atomic():
        keys = list(
            EventSeat.objects.filter(
                event=event, seat_key__in=seat_keys, status=SeatStatus.AVAILABLE
            ).values_list("seat_key", flat=True)
        )
        if not keys:
            return 0
        changed = EventSeat.objects.filter(
            event=event, seat_key__in=keys, status=SeatStatus.AVAILABLE
        ).update(status=SeatStatus.BLOCKED, block_note=note)
        # capacity = seats that can be sold; a free seat existed, so this never
        # drops capacity under sold_total.
        Event.objects.filter(pk=event.pk).update(
            capacity_total=models.F("capacity_total") - changed
        )
    log_action("seating.seat_blocked", event, actor=actor,
               metadata={"seats": keys, "note": note})
    return changed


def unblock_seats(event: Event, seat_keys: list[str], actor=None) -> int:
    with transaction.atomic():
        keys = list(
            EventSeat.objects.filter(
                event=event, seat_key__in=seat_keys, status=SeatStatus.BLOCKED
            ).values_list("seat_key", flat=True)
        )
        if not keys:
            return 0
        changed = EventSeat.objects.filter(
            event=event, seat_key__in=keys, status=SeatStatus.BLOCKED
        ).update(status=SeatStatus.AVAILABLE, block_note="")
        Event.objects.filter(pk=event.pk).update(
            capacity_total=models.F("capacity_total") + changed
        )
    log_action("seating.seat_unblocked", event, actor=actor, metadata={"seats": keys})
    return changed


def move_seat(event: Event, from_key: str, to_key: str, actor=None, notify: bool = True):
    """
    Staff moves one bought/held seat to a free one. The order, its price and the
    ticket code stay the same; only the seat changes (ticket + order labels).
    Returns the updated order.
    """
    from apps.orders.models import Order
    from apps.tickets.models import Ticket, TicketStatus

    if from_key == to_key:
        raise ValidationError("Wybierz inne miejsce docelowe.")
    with transaction.atomic():
        source = EventSeat.objects.select_for_update().filter(
            event=event, seat_key=from_key, status__in=[SeatStatus.HELD, SeatStatus.SOLD]
        ).first()
        if source is None:
            raise ValidationError("To miejsce nie jest już sprzedane ani zarezerwowane.")
        order = source.order
        taken = EventSeat.objects.filter(
            event=event, seat_key=to_key, status=SeatStatus.AVAILABLE
        ).update(status=source.status, order=order)
        if not taken:
            raise ValidationError("Miejsce docelowe nie jest wolne.")
        target = EventSeat.objects.get(event=event, seat_key=to_key)
        EventSeat.objects.filter(pk=source.pk).update(status=SeatStatus.AVAILABLE, order=None)
        Ticket.objects.filter(order=order, seat=source).exclude(
            status__in=[TicketStatus.REFUNDED, TicketStatus.CANCELLED]
        ).update(seat=target)
        keys = [to_key if k == from_key else k for k in order.seat_keys]
        Order.objects.filter(pk=order.pk).update(
            seat_keys=keys, seat_labels=labels_for_keys(event, keys)
        )
    order.refresh_from_db()
    log_action("seating.seat_moved", order, actor=actor,
               metadata={"from": source.label, "to": target.label})
    if notify and order.status == "PAID" and order.tickets.exists():
        from apps.tickets.services import queue_seat_change_email

        queue_seat_change_email(order, source.label, target.label)
    return order


def labels_for_keys(event: Event, keys: list[str]) -> list[str]:
    seats = {s.seat_key: s for s in EventSeat.objects.filter(event=event, seat_key__in=keys)}
    return [seats[k].label for k in keys if k in seats]


# --- selection rules (buyer) -------------------------------------------------------


def selection_problem(event: Event, seats: list[EventSeat], *, allow_partial_table=False) -> str:
    """
    Business rules for a buyer's selection ('' = fine):
    - a TABLE-only table goes whole (every seat not blocked by staff),
    - single seats count against max_tickets_per_order; whole tables don't (D3),
    - never more than MAX_SEATS_PER_ORDER seats at once.
    """
    if not seats:
        return "Wybierz co najmniej jedno miejsce."
    if len(seats) > MAX_SEATS_PER_ORDER:
        return f"W jednym zamówieniu można kupić najwyżej {MAX_SEATS_PER_ORDER} miejsc."
    chosen = defaultdict(set)
    for seat in seats:
        chosen[seat.group_key].add(seat.seat_key)
    group_seats = defaultdict(set)
    group_info = {}
    for seat in EventSeat.objects.filter(event=event, group_key__in=chosen).exclude(
        status=SeatStatus.BLOCKED
    ):
        group_seats[seat.group_key].add(seat.seat_key)
        group_info[seat.group_key] = seat
    single = 0
    for group_key, keys in chosen.items():
        info = group_info.get(group_key)
        whole = keys >= group_seats[group_key]
        if info and info.sell_mode == SellMode.TABLE and not whole and not allow_partial_table:
            return (f"Stolik {info.group_label} sprzedawany jest tylko w całości "
                    f"({len(group_seats[group_key])} miejsc).")
        if not (whole and info and info.sell_mode in (SellMode.TABLE, SellMode.BOTH)):
            single += len(keys)
    if single > event.max_tickets_per_order:
        return (f"Pojedynczych miejsc można kupić najwyżej {event.max_tickets_per_order} "
                f"w jednym zamówieniu (cały stolik nie wlicza się do limitu).")
    return ""


# --- read models ----------------------------------------------------------------------


def public_seat_states(event: Event, mine: set[str] | None = None) -> dict[str, str]:
    """seat_key -> free / taken / blocked / mine — never who bought what."""
    mine = mine or set()
    states = {}
    for key, status in EventSeat.objects.filter(event=event).values_list("seat_key", "status"):
        if key in mine:
            states[key] = "mine"
        elif status == SeatStatus.AVAILABLE:
            states[key] = "free"
        elif status == SeatStatus.BLOCKED:
            states[key] = "blocked"
        else:
            states[key] = "taken"
    return states


def seat_map_payload(event: Event) -> dict | None:
    """Everything the renderer needs (room + frozen layout + seat meta)."""
    seat_map = getattr(event, "seat_map", None)
    if seat_map is None:
        return None
    return {"room": seat_map.room_snapshot, "layout": seat_map.layout_snapshot}


def zone_prices(event: Event, base) -> dict:
    """{code: {"name", "percent", "price"}} for the buyer's map and legend
    (prices as strings — they travel through JSON, never through {{ }})."""
    from .layout import zone_price

    seat_map = getattr(event, "seat_map", None)
    if seat_map is None or base is None:
        return {}
    return {
        z["code"]: {"name": z.get("name", ""), "percent": z["percent"],
                    "price": str(zone_price(base, z["percent"]))}
        for z in seat_map.layout_snapshot.get("zones", []) or []
    }


def occupancy_by_group(event: Event) -> list[dict]:
    """Per table/row: sold, held, free, blocked — for the staff map and report."""
    groups: dict[str, dict] = {}
    for seat in EventSeat.objects.filter(event=event).order_by("sort_index"):
        g = groups.setdefault(seat.group_key, {
            "key": seat.group_key, "kind": seat.group_kind, "label": seat.group_label,
            "sell_mode": seat.get_sell_mode_display(), "total": 0,
            SeatStatus.SOLD: 0, SeatStatus.HELD: 0, SeatStatus.AVAILABLE: 0,
            SeatStatus.BLOCKED: 0,
        })
        g["total"] += 1
        g[seat.status] += 1
    return [
        {**g, "sold": g[SeatStatus.SOLD], "held": g[SeatStatus.HELD],
         "free": g[SeatStatus.AVAILABLE], "blocked": g[SeatStatus.BLOCKED]}
        for g in groups.values()
    ]


def default_room() -> Room | None:
    return Room.objects.order_by("pk").first()

