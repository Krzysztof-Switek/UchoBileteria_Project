from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from . import services
from .models import Event, EventStatus


def event_list(request):
    now = timezone.now()
    events = Event.objects.filter(
        status=EventStatus.PUBLISHED, end_at__gte=now
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
        "is_cancelled": event.status == EventStatus.CANCELLED,
        "quantity_choices": range(1, event.max_tickets_per_order + 1),
    }
    return render(request, "events/detail.html", context)
