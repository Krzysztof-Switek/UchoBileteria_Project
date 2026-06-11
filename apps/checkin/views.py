from datetime import timedelta

from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.events.models import Event, EventStatus

from . import services
from .services import ScanResult

# Every entrance-control view requires the dedicated check-in permission
# (DOOR_STAFF group); 403 instead of a login-redirect loop for users without it.
can_scan = permission_required("tickets.checkin_ticket", raise_exception=True)


def _scannable_events():
    """Events staff may check people into: published, not long finished."""
    cutoff = timezone.now() - timedelta(hours=12)
    return Event.objects.filter(
        status__in=[EventStatus.PUBLISHED, EventStatus.FINISHED], end_at__gte=cutoff
    ).order_by("start_at")


@login_required
@can_scan
def event_select(request):
    return render(request, "checkin/event_select.html", {"events": _scannable_events()})


@login_required
@can_scan
def scanner(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    return render(request, "checkin/scanner.html", {"event": event})


@login_required
@can_scan
@require_POST
def scan(request, event_id):
    """Receives the decoded QR text (or a short code) and returns the verdict."""
    event = get_object_or_404(Event, pk=event_id)
    scanned = request.POST.get("code", "").strip()

    # The QR encodes a verify URL - extract the token parameter if present.
    token = scanned
    if "t=" in scanned:
        token = scanned.split("t=", 1)[1].split("&", 1)[0]

    try:
        if "-" in scanned and len(scanned) <= 12:  # looks like a short code
            result, ticket = services.check_in_by_short_code(scanned, event, request.user)
        else:
            result, ticket = services.check_in_by_token(token, event, request.user)
    except Exception:  # noqa: BLE001 - scanner must always answer
        result, ticket = ScanResult.SERVER_ERROR, None

    return JsonResponse(
        {
            "result": result,
            "ok": result == ScanResult.VALID_CHECKED_IN,
            "message": ScanResult.MESSAGES[result],
            "ticket_code": ticket.short_code if ticket else "",
            "buyer_email": ticket.buyer_email if ticket else "",
        }
    )


@login_required
@can_scan
def verify(request):
    """
    Landing endpoint for the URL baked into QR codes - lets staff scan
    tickets with a plain phone camera while logged in.
    """
    token = request.GET.get("t", "")
    event_id = request.GET.get("event")
    if event_id:
        event = get_object_or_404(Event, pk=event_id)
        result, ticket = services.check_in_by_token(token, event, request.user)
        context = {
            "result": result,
            "ok": result == ScanResult.VALID_CHECKED_IN,
            "message": ScanResult.MESSAGES[result],
            "ticket": ticket,
            "event": event,
        }
        return render(request, "checkin/verify_result.html", context)
    # No event chosen yet: ask which gate this is.
    return render(
        request,
        "checkin/verify_pick_event.html",
        {"events": _scannable_events(), "token": token},
    )


@login_required
@can_scan
def search(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    query = request.GET.get("q", "")
    tickets = services.find_tickets(event, query) if query else []
    return render(
        request,
        "checkin/search.html",
        {"event": event, "query": query, "tickets": tickets},
    )


@login_required
@can_scan
@require_POST
def check_in_code(request, event_id):
    """Manual check-in from the search screen."""
    event = get_object_or_404(Event, pk=event_id)
    code = request.POST.get("code", "")
    result, ticket = services.check_in_by_short_code(code, event, request.user)
    return render(
        request,
        "checkin/verify_result.html",
        {
            "result": result,
            "ok": result == ScanResult.VALID_CHECKED_IN,
            "message": ScanResult.MESSAGES[result],
            "ticket": ticket,
            "event": event,
        },
    )


@login_required
@can_scan
def emergency_list(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="lista-awaryjna-{event.slug}.csv"'
    )
    response.write("﻿")  # BOM so Excel opens UTF-8 correctly
    services.write_emergency_list(event, response, actor=request.user)
    return response

