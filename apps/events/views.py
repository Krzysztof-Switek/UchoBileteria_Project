from datetime import UTC

from django.db.models import Q
from django.http import Http404, HttpResponse
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
    return render(request, "events/detail.html", context)


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
        "PRODID:-//Bileteria UCHO//PL\r\n"
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
