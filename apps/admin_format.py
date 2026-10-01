"""Shared admin helpers: dd.mm.rr gg:mm dates (local time) and per-event lists."""

from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone


def short_dt(value):
    """dd.mm.rr gg:mm in local time — the compact format used in admin lists."""
    return timezone.localtime(value).strftime("%d.%m.%y %H:%M") if value else "—"


def dt_column(field, label):
    """list_display column showing `field` as short_dt, still sortable by it:
    `created_short = dt_column("created_at", "utworzono")`."""

    @admin.display(description=label, ordering=field)
    def column(self, obj):
        return short_dt(getattr(obj, field))

    return column


class PerEventChangelistMixin:
    """Orders, tickets, e-mails and both logs are browsed per event (2026-10-01):
    the bare changelist redirects to the event picker. Any explicit filter
    (event, status — e.g. the dashboard "Wymaga uwagi" links) shows the list
    as usual, and ?wszystkie=1 gives the full list on demand. The path
    (breadcrumbs) goes through the event: Start › Wybierz wydarzenie › event ›
    list › record — see the two templates below."""

    event_lookup = ""  # changelist query param holding the event id
    event_attr = "event"  # dotted path from a record to its event
    list_label = ""
    change_list_template = "admin/ucho_per_event_change_list.html"
    change_form_template = "admin/ucho_per_event_change_form.html"

    def event_of_record(self, obj):
        value = obj
        for part in self.event_attr.split("."):
            value = getattr(value, part, None)
            if value is None:
                return None
        return value

    def changelist_view(self, request, extra_context=None):
        if request.method == "GET" and not request.GET:
            return redirect("admin:orders_by_event")
        if "wszystkie" in request.GET:
            request.GET = request.GET.copy()
            del request.GET["wszystkie"]
        extra_context = dict(extra_context or {})
        extra_context["ucho_list_label"] = self.list_label
        event_id = request.GET.get(self.event_lookup)
        if event_id:
            from apps.events.models import Event

            event = Event.objects.filter(pk=event_id).first()
            if event is not None:
                extra_context["ucho_event"] = event
                extra_context["title"] = (
                    f"{self.list_label} — {event.title} ({short_dt(event.start_at)})"
                )
        return super().changelist_view(request, extra_context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = dict(extra_context or {})
        obj = self.get_object(request, object_id)
        event = self.event_of_record(obj) if obj is not None else None
        if event is not None:
            changelist = reverse(f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist")
            extra_context.update({
                "ucho_event": event,
                "ucho_list_label": self.list_label,
                "ucho_event_list_url": f"{changelist}?{self.event_lookup}={event.pk}",
            })
        return super().change_view(request, object_id, form_url, extra_context)
