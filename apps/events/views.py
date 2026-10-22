from datetime import UTC
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone

from . import services
from .models import Event, EventStatus


def event_list(request):
    now = timezone.now()
    # end_at is optional — fall back to start_at when unset.
    events = Event.objects.filter(
        Q(end_at__gte=now) | Q(end_at__isnull=True, start_at__gte=now),
        status=EventStatus.PUBLISHED,
    ).order_by("start_at")
    return render(request, "events/list.html", {"events": events})


def event_detail(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if event.status == EventStatus.DRAFT:
        raise Http404
    active_pool = services.get_active_pool(event)
    context = {
        "event": event,
        "sales_state": services.get_sales_state(event),
        "active_pool": active_pool,
        # KUP-02: str(Decimal) never uses a locale comma, unlike {{ }}
        # (see the RAP-03 SVG bug) — needed as a plain number for the JS total.
        "active_pool_price_raw": str(active_pool.price_gross) if active_pool else None,
        "next_pool": services.get_next_pool(event) if active_pool else None,
        "is_cancelled": event.status == EventStatus.CANCELLED,
        "quantity_choices": range(1, event.max_tickets_per_order + 1),
    }
    if event.is_seated:
        from apps.seating.services import MAX_SEATS_PER_ORDER, seat_map_payload, zone_prices

        payload = seat_map_payload(event)
        if payload:
            context["seat_config"] = {
                **payload,
                "price": context["active_pool_price_raw"],
                "currency": active_pool.currency if active_pool else "",
                "states_url": reverse("events:seat_states", args=[event.slug]),
                "preselected": [k for k in request.GET.get("wybrane", "").split(",") if k][:60],
                "max_single": event.max_tickets_per_order,
                "max_total": MAX_SEATS_PER_ORDER,
                "zone_prices": zone_prices(
                    event, active_pool.price_gross if active_pool else None
                ),
            }
            zones = context["seat_config"]["zone_prices"]
            context["zone_price_list"] = [  # Decimal -> the template formats "108,00"
                {"code": code, **zone, "price": Decimal(zone["price"])}
                for code, zone in zones.items()
            ]
            context["prefill_email"] = request.GET.get("email", "")[:254]
    return render(request, "events/detail.html", context)


def seat_states(request, slug):
    """Light JSON polled by the buyer's map: seat_key -> free/taken/blocked
    (plus "mine" for the seats of the buyer's own unpaid order)."""
    from apps.orders.models import Order
    from apps.seating.services import public_seat_states

    event = get_object_or_404(Event, slug=slug, status=EventStatus.PUBLISHED)
    mine: set[str] = set()
    order_id = request.GET.get("zamowienie")
    if order_id:
        try:
            order = Order.objects.filter(id=order_id, event=event).first()
        except (ValueError, ValidationError):
            order = None
        if order and order.status in ("CREATED", "PAYMENT_PENDING"):
            mine = set(order.seat_keys)
    event.refresh_from_db(fields=["sold_total", "capacity_total"])
    response = JsonResponse({
        "states": public_seat_states(event, mine),
        "sales_state": services.get_sales_state(event),
    })
    response["Cache-Control"] = "no-store"
    return response


def sales_start_reminder(request, slug):
    """KUP-07: .ics download so a "sprzedaż wkrótce" visitor can get a
    calendar reminder without us needing an email/notification system."""
    event = get_object_or_404(Event, slug=slug)
    dtstamp = timezone.now().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    dtstart = event.sales_start_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    event_url = request.build_absolute_uri(reverse("events:detail", args=[event.slug]))
    ics = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Bileteria Scena Ucho//PL\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:sprzedaz-{event.pk}@bileteria-ucho\r\n"
        f"DTSTAMP:{dtstamp}\r\n"
        f"DTSTART:{dtstart}\r\n"
        f"SUMMARY:Start sprzedaży biletów — {event.title}\r\n"
        f"DESCRIPTION:Sprzedaż biletów na {event.title} właśnie się zaczyna. {event_url}\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    response = HttpResponse(ics, content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="przypomnienie-{event.slug}.ics"'
    return response


def terms(request):
    """Regulamin sprzedaży biletów — accepted with every purchase."""
    from django.conf import settings

    from apps.orders.terms import TERMS_VERSION

    return render(request, "legal/regulamin.html", {
        "version": TERMS_VERSION,
        "club": {
            "name": settings.CLUB_NAME,
            "legal_name": getattr(settings, "CLUB_LEGAL_NAME", "") or settings.CLUB_NAME,
            "nip": getattr(settings, "CLUB_NIP", ""),
            "address": settings.CLUB_ADDRESS,
            "email": settings.CLUB_CONTACT_EMAIL,
            "phone": settings.CLUB_PHONE,
        },
    })
