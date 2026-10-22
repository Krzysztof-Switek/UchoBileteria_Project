from datetime import datetime, time, timedelta

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.utils import unquote
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from django.forms.models import BaseInlineFormSet
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html_join
from django.utils.safestring import mark_safe

from apps.admin_badges import choice_badge
from apps.admin_format import dt_column, short_dt

from . import services
from .models import (
    Event,
    EventStatus,
    PoolManualStatus,
    SeatingMode,
    TicketPool,
    online_sales_cutoff,
)

SALES_STATE_LABELS = {
    "NOT_STARTED": "Nie rozpoczęta",
    "ON_SALE": "W sprzedaży",
    "PAUSED": "Wstrzymana",
    "SOLD_OUT": "Wyprzedana",
    "CLOSED": "Zakończona",
}
SALES_STATE_TONES = {
    "NOT_STARTED": "info",
    "ON_SALE": "success",
    "PAUSED": "warning",
    "SOLD_OUT": "warning",
    "CLOSED": "neutral",
}


class TicketPoolFormSet(BaseInlineFormSet):
    """Pools are created together with the event now (OBS-08 follow-up).
    Cross-row rules checked here: the capacity budget, the hard "sales end
    before the concert" cutoff, and no two pools overlapping in time — the
    calendar widget in the admin template enforces all three client-side
    too, but this is the actual guarantee."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        # A pool tickets were sold from can never be deleted (06.10.2026): the
        # buyers' orders and tickets point at it. Refused with a clear reason.
        blocked = []
        for form in self.forms:
            pool = form.instance
            if form.cleaned_data.get("DELETE") and pool.pk:
                orders = pool.orders.count()
                if orders or pool.sold_count:
                    blocked.append(f"„{pool.name or 'pula bez nazwy'}” (sprzedane bilety: "
                                   f"{pool.sold_count}, zamówienia: {orders})")
        if blocked:
            raise ValidationError(
                "Nie można usunąć puli, z której sprzedano bilety: " + "; ".join(blocked)
                + ". Kupujący mają z niej ważne bilety i zamówienia. Zamiast usuwać: zakończ "
                "sprzedaż puli (ustaw wcześniejszy koniec sprzedaży albo zmniejsz ilość do "
                "liczby sprzedanych) — albo odwołaj / przenieś wydarzenie."
            )
        rows = [
            form.cleaned_data
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE")
        ]
        # Names are optional (the UI shows "Pula N" by row position when
        # blank) — mirror that here so error messages name the right pool.
        display_names = [row.get("name") or f"Pula {i + 1}" for i, row in enumerate(rows)]

        total_capacity = sum(row.get("capacity") or 0 for row in rows)
        if total_capacity > settings.CLUB_MAX_CAPACITY:
            raise ValidationError(
                f"Suma biletów w pulach ({total_capacity}) przekracza pojemność "
                f"klubu ({settings.CLUB_MAX_CAPACITY} osób)."
            )

        concert_start = getattr(self.instance, "start_at", None)
        if concert_start:
            concert_midnight = timezone.make_aware(
                datetime.combine(timezone.localtime(concert_start).date(), time.min)
            )
            for row, display_name in zip(rows, display_names, strict=True):
                end = row.get("sales_end_at")
                if end and end > concert_midnight:
                    raise ValidationError(
                        f"Pula „{display_name}” musi kończyć się przed dniem "
                        f"koncertu ({timezone.localtime(concert_start).date():%d.%m.%Y})."
                    )

        dated = [
            (row, name)
            for row, name in zip(rows, display_names, strict=True)
            if row.get("sales_start_at") and row.get("sales_end_at")
        ]
        for i, (a, a_name) in enumerate(dated):
            for b, b_name in dated[i + 1 :]:
                a_start, a_end = a["sales_start_at"], a["sales_end_at"]
                b_start, b_end = b["sales_start_at"], b["sales_end_at"]
                if a_start < b_end and b_start < a_end:
                    raise ValidationError(
                        f"Pule „{a_name}” i „{b_name}” nachodzą na siebie w czasie."
                    )


def _fmt(dt) -> str:
    return timezone.localtime(dt).strftime("%d.%m.%Y %H:%M")


class EventAdminForm(forms.ModelForm):
    """Event form with the seating choice made right at the start (step 1 of the
    wizard) and the times checked on the spot — not only at publish."""

    seating_choice = forms.ChoiceField(
        label="Sprzedaż",
        choices=SeatingMode.choices,
        initial=SeatingMode.GENERAL,
        widget=forms.RadioSelect,
        required=False,
        help_text="Miejsca numerowane: kupujący wybiera konkretne miejsca na planie sali.",
    )
    seating_layout = forms.ModelChoiceField(
        label="Układ sali",
        queryset=None,
        required=False,
        empty_label="— wybierz układ —",
        help_text="Układy tworzy się w „Planach sali”. Pojemność = liczba miejsc w układzie.",
    )

    class Meta:
        model = Event
        fields = ["title", "max_tickets_per_order", "description", "gates_open_at", "start_at",
                  "end_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.seating.models import SeatingLayout

        self.fields["seating_layout"].queryset = SeatingLayout.objects.filter(
            is_archived=False
        ).order_by("name")
        self.fields["seating_layout"].label_from_instance = (
            lambda lay: f"{lay.name} — {lay.seat_count} miejsc"
        )

    # set by EventAdmin.get_form: the request (for the "confirm changes" step)
    ucho_request = None

    def clean(self):
        cleaned = super().clean()
        request = self.ucho_request
        if request is not None and getattr(request, "ucho_pending_changes", None):
            # Tickets are sold: nothing is saved until the changes are confirmed
            # (the panel above the form lists them; 06.10.2026).
            self.add_error(None, "Na to wydarzenie sprzedano już bilety — sprawdź zmiany i "
                                 "potwierdź zapis.")
        start, end = cleaned.get("start_at"), cleaned.get("end_at")
        gates = cleaned.get("gates_open_at")
        if start and end and end <= start:
            if end.date() == start.date():
                # "Koniec 3:00" for a 23:00 concert means the night after — the
                # wizard picks one day, so a time before the start is the next day.
                end = end + timedelta(days=1)
                cleaned["end_at"] = end
            if end <= start:
                self.add_error("end_at", (
                    f"Koniec ({_fmt(end)}) jest przed startem koncertu ({_fmt(start)})."
                ))
        if start and gates and gates > start:
            self.add_error("gates_open_at", (
                f"Bramki ({_fmt(gates)}) muszą otworzyć się przed startem koncertu "
                f"({_fmt(start)}) albo w tym samym momencie."
            ))
        if "seating_choice" in self.fields and self.instance.pk is None:
            if cleaned.get("seating_choice") == SeatingMode.SEATED:
                if not cleaned.get("seating_layout"):
                    self.add_error("seating_layout", "Wybierz układ sali dla miejsc numerowanych.")
            else:
                cleaned["seating_layout"] = None
        return cleaned


class TicketPoolInline(admin.TabularInline):
    model = TicketPool
    formset = TicketPoolFormSet
    extra = 1
    # currency is always PLN; manual_status (force open/closed) only makes
    # sense once a pool is live, not while it's still being created here —
    # it stays reachable from the standalone TicketPoolAdmin actions.
    exclude = ["currency", "manual_status"]

    def get_fields(self, request, obj=None):
        # Sales window last — it's the calendar-driven button on the add
        # wizard, visually the "heaviest" field, and reads best as the final
        # thing you set once name/price/capacity/sellout policy are in.
        fields = ["name", "price_gross", "capacity", "on_sellout", "sales_start_at", "sales_end_at"]
        if obj is not None:
            # Meaningless while the event is still being created (always
            # zero) — sales figures belong in reports/the dashboard, not
            # here. Real once the event exists and pools can actually sell.
            fields.insert(3, "sold_count")
        return fields

    def get_readonly_fields(self, request, obj=None):
        return ["sold_count"] if obj is not None else []

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        django_check = formset.form.hand_clean_DELETE

        def hand_clean_DELETE(form):
            # Our own clear message instead of Django's generic "Usunięcie pula
            # biletów … chronionych obiektów" for a pool tickets were sold from.
            pool = form.instance
            if form.cleaned_data.get("DELETE") and pool.pk:
                orders = pool.orders.count()
                if orders or pool.sold_count:
                    raise ValidationError(
                        f"Nie można usunąć puli „{pool.name or 'pula bez nazwy'}” — sprzedano "
                        f"z niej {pool.sold_count} bil. (zamówienia: {orders}), kupujący mają "
                        "ważne bilety. Zamiast usuwać: zakończ sprzedaż puli (wcześniejszy "
                        "koniec sprzedaży albo ilość = sprzedane) — albo odwołaj / przenieś "
                        "wydarzenie."
                    )
            return django_check(form)

        formset.form = type(formset.form.__name__, (formset.form,),
                            {"hand_clean_DELETE": hand_clean_DELETE})
        return formset

    def get_extra(self, request, obj=None, **kwargs):
        # editing: only the pools that really exist (an empty extra row looked
        # like a second pool) — "Dodaj pulę" adds one; the wizard starts with one
        return 0 if obj is not None else 1


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "start_short",
        "status",
        "sales_state",
        "pools_summary",
        "sold_total",
        "revenue_paid",
        "sales_start_short",
        "sales_end_short",
    ]
    list_filter = ["status"]
    search_fields = ["title", "slug"]
    readonly_fields = [
        "status",
        "seating_mode",
        "sold_total",
        "revenue_paid",
        "calendar_event_id",
        "created_by",
        "updated_by",
        "created_at",
        "updated_at",
    ]
    date_hierarchy = "start_at"
    form = EventAdminForm
    inlines = [TicketPoolInline]
    actions = ["publish_events", "cancel_events", "cancel_events_with_refunds"]
    change_form_template = "admin/events/event/change_form.html"

    # Fields that only make sense once the event exists (live stats, system
    # integrations) — kept off the "add" form so a new event starts with just
    # the essentials.
    STATS_FIELDSETS = [
        (
            "Stan sprzedaży",
            {
                "description": "Aktualizowane automatycznie na podstawie zamówień.",
                "fields": ["sold_total", "revenue_paid"],
            },
        ),
        (
            "Integracje i historia",
            {
                "description": "Uzupełniane automatycznie przez system.",
                "classes": ["collapse"],
                "fields": [
                    "calendar_event_id",
                    "created_by",
                    "updated_by",
                    "created_at",
                    "updated_at",
                ],
            },
        ),
    ]

    def get_fieldsets(self, request, obj=None):
        fieldsets = [
            (
                "Podstawowe informacje",
                {
                    "description": (
                        ""
                        if obj is None
                        else "Przyciski na górze strony: zapis, publikacja, odwołanie "
                        "(powiadamia kupujących: zwrot albo nowy termin)."
                    ),
                    "fields": (
                        ["title", "seating_choice", "seating_layout", "max_tickets_per_order"]
                        if obj is None
                        else ["title", "status", "seating_mode", "max_tickets_per_order"]
                    ),
                },
            ),
            (
                "Opis",
                {
                    "description": (
                        "Treść widoczna na publicznej stronie wydarzenia. Miejsce "
                        "(Podwórko Art / Scena Ucho, ul. Świętego Piotra 2, 81-347 Gdynia) jest "
                        "takie samo dla każdego wydarzenia."
                    ),
                    "fields": ["description"],
                },
            ),
            (
                "Termin wydarzenia",
                {
                    "description": "",
                    "fields": ["gates_open_at", "start_at", "end_at"],
                },
            ),
        ]
        if obj is not None:
            fieldsets += self.STATS_FIELDSETS
        return fieldsets

    def _conflict_events(self, exclude_pk=None) -> list:
        """Published events' dates, for the add/change form's conflict calendar."""
        qs = Event.objects.filter(status=EventStatus.PUBLISHED).only("start_at", "title")
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)
        return [{"date": e.start_at.date().isoformat(), "title": e.title} for e in qs]

    def add_view(self, request, form_url="", extra_context=None):
        extra_context = dict(extra_context or {})
        extra_context["ucho_calendar_events"] = self._conflict_events()
        extra_context["ucho_max_capacity"] = settings.CLUB_MAX_CAPACITY
        # step 1: preview of the chosen layout + its seat count for the pools step
        from apps.seating.models import SeatingLayout

        layouts = SeatingLayout.objects.filter(is_archived=False).select_related("room")
        extra_context["ucho_layouts"] = {
            str(lay.pk): {"seats": lay.seat_count, "name": lay.name, "layout": lay.data,
                          "room": {"width_cm": lay.room.width_cm, "depth_cm": lay.room.depth_cm,
                                   **lay.room.geometry}}
            for lay in layouts
        }
        # No slug exists yet — the wizard only ever needs the URL shape (the
        # actual slug is filled in client-side as a live preview while typing
        # the title, see change_form.html).
        extra_context["ucho_public_url_template"] = request.build_absolute_uri(
            reverse("events:detail", args=["__slug__"])
        )
        return super().add_view(request, form_url, extra_context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = dict(extra_context or {})
        extra_context["ucho_calendar_events"] = self._conflict_events(exclude_pk=object_id)
        event = self.get_object(request, object_id)
        if event is not None:
            extra_context["ucho_public_url"] = request.build_absolute_uri(
                reverse("events:detail", args=[event.slug])
            )
            # Event-page tiles: gate (event day only in LIVE), lists, report.
            from apps.checkin.services import gate_status

            extra_context["ucho_gate"] = gate_status(event)
            extra_context["ucho_can_delete"] = self.has_delete_permission(request, event)
            extra_context["ucho_can_cancel"] = (
                request.user.has_perm("events.change_event")
                and event.status not in (EventStatus.CANCELLED, EventStatus.FINISHED)
                and (not self._buyer_orders(event).exists() or self.has_refund_permission(request))
            )
        return super().change_view(request, object_id, form_url, extra_context)

    # -- cancelling an event: tell the buyers, refund or new date (06.10.2026) ---------

    def get_urls(self):
        view = self.admin_site.admin_view
        return [
            path("<int:event_id>/odwolaj/", view(self.cancel_view), name="events_event_cancel"),
            path("<int:event_id>/odwolanie/", view(self.cancellation_status_view),
                 name="events_event_cancellation"),
        ] + super().get_urls()

    def cancel_view(self, request, event_id):
        from apps.orders import cancellation

        event = get_object_or_404(Event, pk=event_id)
        if not request.user.has_perm("events.change_event"):
            raise PermissionDenied
        summary = cancellation.cancellation_summary(event)
        if summary["paid_orders"] and not self.has_refund_permission(request):
            raise PermissionDenied
        if event.status in (EventStatus.CANCELLED, EventStatus.FINISHED):
            return redirect("admin:events_event_cancellation", event_id=event.pk)
        candidates = cancellation.replacement_candidates(event)
        error = ""
        if request.method == "POST":
            new_date = request.POST.get("mode") == "nowy-termin"
            replacement = (candidates.filter(pk=request.POST.get("replacement") or 0).first()
                           if new_date else None)
            deadline = None
            new_start = None
            if new_date and replacement is None and request.POST.get("new_date"):
                try:
                    new_start = timezone.make_aware(datetime.strptime(
                        f"{request.POST['new_date']} {request.POST.get('new_time') or '20:00'}",
                        "%Y-%m-%d %H:%M"))
                except ValueError:
                    error = "Nieprawidłowa data lub godzina nowego terminu."
                else:
                    if new_start <= timezone.now() + timedelta(days=1):
                        error = "Nowy termin musi być co najmniej jutro."
            if new_date and replacement is None and new_start is None and not error:
                error = "Kliknij w kalendarzu dzień nowego terminu (albo istniejące wydarzenie)."
            if replacement is not None and request.POST.get("deadline"):
                try:
                    deadline = timezone.make_aware(datetime.combine(
                        datetime.strptime(request.POST["deadline"], "%Y-%m-%d").date(),
                        time(23, 59)))
                except ValueError:
                    error = "Nieprawidłowa data decyzji."
            if not error and not request.POST.get("confirm"):
                error = "Zaznacz potwierdzenie — odwołania nie da się cofnąć."
            if not error:
                from django.db import transaction

                from .services import copy_event_for_new_date

                try:
                    with transaction.atomic():
                        if new_start is not None:
                            replacement = copy_event_for_new_date(event, new_start,
                                                                  actor=request.user)
                        result = cancellation.cancel_event_for_buyers(
                            event, actor=request.user, reason=request.POST.get("reason", ""),
                            replacement=replacement, deadline=deadline)
                except ValidationError as exc:
                    error = " ".join(exc.messages)
                else:
                    if replacement is not None:
                        messages.success(request, (
                            f"Przeniesiono „{event.title}” na "
                            f"{timezone.localtime(replacement.start_at):%d.%m.%Y %H:%M}"
                            + (" (utworzono nowe wydarzenie)" if new_start else "")
                            + f". Kupujący poproszeni o potwierdzenie: {result['notified']}."))
                    else:
                        messages.warning(request, (
                            f"Odwołano „{event.title}”. Powiadomieni kupujący: "
                            f"{result['notified']}, zwroty zlecone: {result['refunded']}"
                            + (f", zwroty NIEUDANE: {result['refund_failed']}"
                               if result["refund_failed"] else "") + "."))
                    return redirect("admin:events_event_cancellation", event_id=event.pk)
        # the system calendar: every event around the coming months; only future
        # draft/published ones can become the new date
        selectable = {e.pk for e in candidates}
        now = timezone.now()
        calendar = []
        for e in Event.objects.filter(start_at__gte=now - timedelta(days=31),
                                      start_at__lte=now + timedelta(days=550)).order_by("start_at"):
            local = timezone.localtime(e.start_at)
            calendar.append({
                "id": e.pk, "title": e.title, "date": local.strftime("%Y-%m-%d"),
                "time": local.strftime("%H:%M"), "status": e.get_status_display(),
                "selectable": e.pk in selectable, "current": e.pk == event.pk,
                "seated": e.is_seated, "free": max(e.capacity_total - e.sold_total, 0),
                "deadline": timezone.localtime(
                    cancellation.default_deadline(e)).strftime("%Y-%m-%d")
                if e.pk in selectable else "",
                "message": cancellation.default_message(event, e) if e.pk in selectable else "",
            })
        context = {
            **self.admin_site.each_context(request),
            "title": f"Odwołaj lub przenieś — {event.title}",
            "event": event,
            "summary": summary,
            "calendar": calendar,
            "refund_message": cancellation.default_message(event),
            # e-mail preview on the screen: one real buyer as the example
            "example_order": event.orders.filter(status="PAID").order_by("created_at").first(),
            # for a day clicked in the calendar: the text with a marker for the date
            "move_message": cancellation.default_message(
                event, new_start=event.start_at).replace(
                cancellation._when(event.start_at), "{STARY}", 1).replace(
                cancellation._when(event.start_at), "{NOWY}", 1),
            "event_time": timezone.localtime(event.start_at).strftime("%H:%M"),
            "event_when": cancellation._when(event.start_at),
            "min_days": cancellation.MIN_DECISION_DAYS,
            "error": error,
            "posted": request.POST if request.method == "POST" else {},
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/events/event/cancel.html", context)

    def cancellation_status_view(self, request, event_id):
        from apps.orders.models import CancellationStatus, Order

        event = get_object_or_404(Event, pk=event_id)
        if not request.user.has_perm("events.view_event"):
            raise PermissionDenied
        if request.method == "POST":
            # manager refunds now: everyone still deciding, or one order / a retry
            from apps.orders.cancellation import refund_now

            if not self.has_refund_permission(request):
                raise PermissionDenied
            pending = Order.objects.filter(event=event, status="PAID")
            if request.POST.get("order"):
                pending = pending.filter(pk=request.POST["order"])
            elif request.POST.get("confirm") != "1":
                messages.error(request, "Zaznacz potwierdzenie zwrotu dla wszystkich czekających.")
                return redirect("admin:events_event_cancellation", event_id=event.pk)
            else:
                pending = pending.filter(cancellation_status=CancellationStatus.AWAITING)
            result = refund_now(list(pending), actor=request.user)
            messages.success(request, f"Zwroty zlecone: {result['refunded']}"
                             + (f", nieudane: {result['refund_failed']}"
                                if result["refund_failed"] else "") + ".")
            return redirect("admin:events_event_cancellation", event_id=event.pk)
        orders = list(Order.objects.filter(
            models.Q(event=event) | models.Q(moved_from_event=event)
        ).exclude(cancellation_status="").select_related("event").order_by("buyer_email"))
        counts = {value: 0 for value, _ in CancellationStatus.choices if value}
        for order in orders:
            counts[order.cancellation_status] = counts.get(order.cancellation_status, 0) + 1
        context = {
            **self.admin_site.each_context(request),
            "title": (f"Zmiana terminu — {event.title}" if event.replacement_event_id
                      else f"Odwołanie — {event.title}"),
            "event": event,
            "orders": orders,
            "counts": [(label, counts.get(value, 0), value)
                       for value, label in CancellationStatus.choices if value],
            "awaiting": counts.get(CancellationStatus.AWAITING, 0),
            "can_refund": self.has_refund_permission(request),
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/events/event/cancellation_status.html", context)

    # -- changes to an event with sold tickets: confirm + decide on e-mails (06.10.2026)

    # A change of these usually matters to buyers -> "send e-mail" pre-ticked;
    # e.g. the gate opening time or the description -> not ticked.
    NOTIFY_BY_DEFAULT = {"title", "start_at", "end_at"}

    def get_form(self, request, obj=None, change=False, **kwargs):
        form = super().get_form(request, obj, change=change, **kwargs)
        return type(form.__name__, (form,), {"ucho_request": request})

    @staticmethod
    def _buyer_orders(event):
        from apps.orders.models import OrderStatus

        return event.orders.filter(status=OrderStatus.PAID)

    @staticmethod
    def _show(value) -> str:
        if value in (None, ""):
            return "—"
        if isinstance(value, datetime):
            return _fmt(value)
        return str(value)

    def _pending_changes(self, request, obj) -> list[dict]:
        """What the submitted form would change (event fields and pools), in
        words; [] when nothing changes or the form has errors (the normal flow
        then shows them)."""
        form_class = super().get_form(request, obj, change=True)
        form = form_class(request.POST, request.FILES, instance=obj)
        if not form.is_valid():
            return []
        changes = []
        for name in form.changed_data:
            if name not in form.base_fields or name in ("seating_choice", "seating_layout"):
                continue
            changes.append({
                "field": name,
                "label": str(form.fields[name].label or name).capitalize(),
                "old": self._show(form.initial.get(name)),
                "new": self._show(form.cleaned_data.get(name)),
            })
        fresh = self.get_object(request, unquote(str(obj.pk)))
        formsets, _ = self._create_formsets(request, fresh, change=True)
        for formset in formsets:
            if not formset.is_valid():
                return []
            for pool_form in formset.forms:
                if not pool_form.has_changed():
                    continue
                name = pool_form.cleaned_data.get("name") or pool_form.instance.name or "pula"
                if pool_form.cleaned_data.get("DELETE"):
                    changes.append({"field": "pool", "label": f"Pula „{name}”",
                                    "old": "jest", "new": "usunięta"})
                elif pool_form.instance.pk is None:
                    changes.append({"field": "pool", "label": f"Pula „{name}”",
                                    "old": "—", "new": "nowa"})
                else:
                    for field in pool_form.changed_data:
                        changes.append({
                            "field": f"pool.{field}",
                            "label": f"Pula „{name}” — "
                                     f"{pool_form.fields[field].label or field}",
                            "old": self._show(pool_form.initial.get(field)),
                            "new": self._show(pool_form.cleaned_data.get(field)),
                        })
        return changes

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if request.method == "POST" and object_id:
            obj = self.get_object(request, unquote(object_id))
            if obj is not None and self._buyer_orders(obj).exists():
                if "_confirm_changes" not in request.POST:
                    changes = self._pending_changes(request, obj)
                    if changes:
                        request.ucho_pending_changes = changes
                        extra_context = {
                            **(extra_context or {}),
                            "ucho_pending_changes": changes,
                            "ucho_notify_default": any(
                                c["field"] in self.NOTIFY_BY_DEFAULT for c in changes),
                            "ucho_buyer_count": self._buyer_orders(obj).count(),
                        }
                else:
                    # confirmed: remember what changed for the e-mail after saving
                    request.ucho_confirmed_changes = self._pending_changes(request, obj)
        return super().changeform_view(request, object_id, form_url, extra_context)

    def _notify_buyers(self, request, event):
        changes = getattr(request, "ucho_confirmed_changes", None)
        if not changes or request.POST.get("_notify_buyers") != "1":
            if changes:
                messages.info(request, "Zapisano zmiany bez powiadamiania kupujących.")
            return
        from apps.auditlog.services import log_action
        from apps.tickets.services import queue_event_change_email

        note = (request.POST.get("_notify_note") or "").strip()[:2000]
        public = [c for c in changes if not c["field"].startswith("pool")]
        orders = list(self._buyer_orders(event))
        for order in orders:
            queue_event_change_email(order, public, note)
        log_action("event.change_notified", event, actor=request.user,
                   metadata={"orders": len(orders), "fields": [c["field"] for c in changes]})
        messages.success(request, f"Wysłano e-mail o zmianach do {len(orders)} kupujących.")

    def has_delete_permission(self, request, obj=None):
        """Deleting removes the event with its pools and seat map for good — only
        for events nobody ordered tickets for. With orders the way out is
        "Odwołaj wydarzenie" (buyers told, refund or new date)."""
        if obj is not None and obj.orders.exists():
            return False
        return super().has_delete_permission(request, obj)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("pools")

    @admin.display(description="start koncertu", ordering="start_at")
    def start_short(self, obj):
        return short_dt(obj.start_at)

    @admin.display(description="start sprzedaży", ordering="sales_start_at")
    def sales_start_short(self, obj):
        return short_dt(obj.sales_start_at)

    @admin.display(description="koniec sprzedaży", ordering="sales_end_at")
    def sales_end_short(self, obj):
        return short_dt(obj.sales_end_at)

    @admin.display(description="pule biletów")
    def pools_summary(self, obj):
        """One line per pool: name, sold/capacity, when its sales end (a pool
        without its own end date sells until the event's online cutoff)."""
        pools = list(obj.pools.all())
        if not pools:
            return "—"
        return format_html_join(
            mark_safe("<br>"),
            '<span style="white-space:nowrap">{}: {}/{} · do {}</span>',
            (
                (
                    pool.name or f"Pula {i}",
                    pool.sold_count,
                    pool.capacity,
                    short_dt(pool.sales_end_at or obj.sales_end_at),
                )
                for i, pool in enumerate(pools, start=1)
            ),
        )

    @admin.display(description="stan sprzedaży")
    def sales_state(self, obj):
        return choice_badge(services.get_sales_state(obj), SALES_STATE_TONES, SALES_STATE_LABELS)

    @admin.display(description="przychód (opłacone)")
    def revenue_paid(self, obj):
        from apps.orders.models import PaymentConfig
        from apps.reports.services import event_revenue_summary

        is_demo = PaymentConfig.is_demo_mode()
        return event_revenue_summary(obj, is_demo=is_demo)["paid"]

    # Actions without `permissions=` are offered to anyone who can merely view
    # the changelist — every action here must name the permission it needs.
    def has_refund_permission(self, request):
        """Cancelling with refunds moves money: same gate as refunding an order."""
        return request.user.has_perm("orders.change_order")

    @admin.action(description="Opublikuj wybrane wydarzenia", permissions=["change"])
    def publish_events(self, request, queryset):
        for event in queryset:
            try:
                services.publish_event(event, actor=request.user)
                messages.success(request, f"Opublikowano: {event}")
            except ValidationError as exc:
                messages.error(request, f"{event}: {'; '.join(exc.messages)}")

    @admin.action(description="Odwołaj wybrane wydarzenia", permissions=["change"])
    def cancel_events(self, request, queryset):
        for event in queryset:
            if self._buyer_orders(event).exists():
                # buyers must be told and offered a refund / the new date
                messages.error(request, f"{event}: są sprzedane bilety — użyj „Odwołaj "
                                        "wydarzenie” na stronie wydarzenia (powiadomienie "
                                        "kupujących, zwrot albo nowy termin).")
                continue
            try:
                services.cancel_event(event, actor=request.user)
                messages.warning(request, f"Odwołano: {event}")
            except ValidationError as exc:
                messages.error(request, f"{event}: {'; '.join(exc.messages)}")

    @admin.action(description="Odwołaj i zwróć wszystkie opłacone zamówienia",
                  permissions=["refund"])
    def cancel_events_with_refunds(self, request, queryset):
        from apps.orders.cancellation import cancel_event_for_buyers

        for event in queryset:
            try:
                summary = cancel_event_for_buyers(event, actor=request.user)
                messages.warning(
                    request,
                    f"Odwołano {event}: powiadomieni {summary['notified']}, zwroty "
                    f"{summary['refunded']}, nieudane {summary['refund_failed']}, anulowane "
                    f"nieopłacone {summary['cancelled_unpaid']}.",
                )
            except ValidationError as exc:
                messages.error(request, f"{event}: {'; '.join(exc.messages)}")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
            # sales_start_at/sales_end_at/capacity_total are no longer entered by
            # hand — they're derived from the pools themselves in save_related()
            # below, once the pools actually exist. Placeholders here only so
            # this first INSERT satisfies the NOT NULL columns.
            obj.sales_start_at = obj.sales_start_at or obj.start_at
            obj.sales_end_at = obj.sales_end_at or obj.start_at
            obj.capacity_total = obj.capacity_total or 1
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        # Keep Google Calendar in sync when a published event is edited.
        if change and obj.status == "PUBLISHED":
            from .calendar import enqueue_calendar_sync
            from .models import CalendarAction

            enqueue_calendar_sync(obj, CalendarAction.UPSERT)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        event = form.instance
        pools = list(event.pools.all())
        update_fields = []
        starts = [p.sales_start_at for p in pools if p.sales_start_at]
        if starts:
            event.sales_start_at = min(starts)
            update_fields.append("sales_start_at")
        # Online sales always stop at midnight before the concert day,
        # regardless of where the last pool's window ends.
        event.sales_end_at = online_sales_cutoff(event.start_at)
        update_fields.append("sales_end_at")
        if event.is_seated:
            # Seated: capacity = seats on the map not blocked by staff — pools only
            # set the price tiers (apps.seating keeps this number in step).
            from apps.seating.models import EventSeat, SeatStatus

            event.capacity_total = EventSeat.objects.filter(event=event).exclude(
                status=SeatStatus.BLOCKED
            ).count()
            update_fields.append("capacity_total")
        elif pools:
            event.capacity_total = sum(p.capacity for p in pools)
            update_fields.append("capacity_total")
        if update_fields:
            event.save(update_fields=update_fields)

        # Seating chosen in step 1 of the wizard: attach the layout now that the
        # pools exist (capacity becomes the number of seats).
        layout = form.cleaned_data.get("seating_layout") if not change else None
        if layout is not None:
            from apps.seating.services import attach_layout

            try:
                for note in attach_layout(event, layout, actor=request.user):
                    messages.warning(request, note)
                messages.success(request, f"Miejsca numerowane: przypięto układ „{layout.name}” "
                                          f"({event.capacity_total} miejsc).")
            except ValidationError as exc:
                messages.error(request, "Nie przypięto układu sali: " + "; ".join(exc.messages))

    def response_add(self, request, obj, post_url_continue=None):
        if "_publish" in request.POST:
            return self._publish_and_redirect(request, obj)
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        self._notify_buyers(request, obj)
        if "_publish" in request.POST:
            return self._publish_and_redirect(request, obj)
        return super().response_change(request, obj)

    def _publish_and_redirect(self, request, obj):
        try:
            services.publish_event(obj, actor=request.user)
            messages.success(request, f"Opublikowano: {obj}")
            return redirect(reverse("admin:events_event_changelist"))
        except ValidationError as exc:
            reasons = "; ".join(exc.messages)
            messages.error(request, f"Zapisano jako szkic — nie udało się opublikować: {reasons}")
            return redirect(reverse("admin:events_event_change", args=[obj.pk]))


@admin.register(TicketPool)
class TicketPoolAdmin(admin.ModelAdmin):
    sales_start_short = dt_column("sales_start_at", "start sprzedaży puli")
    sales_end_short = dt_column("sales_end_at", "koniec sprzedaży puli")
    list_display = [
        "name",
        "event",
        "sales_start_short",
        "sales_end_short",
        "price_gross",
        "capacity",
        "sold_count",
        "manual_status",
        "on_sellout",
        "computed_status",
    ]
    list_filter = ["manual_status", "event"]
    readonly_fields = ["sold_count"]
    actions = ["force_open", "force_close", "set_auto"]

    def has_module_permission(self, request):
        # Pools only ever make sense attached to an event — added and edited
        # via the inline on the event's own page, never standalone. Keeping
        # this ModelAdmin registered (not unregistering it) preserves the
        # cross-event list + force-open/close bulk actions for whoever
        # navigates here directly; it's just off the main app list/nav.
        return False

    def has_delete_permission(self, request, obj=None):
        if obj is not None and (obj.sold_count or obj.orders.exists()):
            return False
        return super().has_delete_permission(request, obj)

    def has_add_permission(self, request):
        return False  # "+ Dodaj" here would create a pool with no event context

    @admin.display(description="status (wyliczony)")
    def computed_status(self, obj):
        for pool, status in services.pools_with_status(obj.event):
            if pool.pk == obj.pk:
                return status
        return "?"

    def _set_manual(self, request, queryset, manual_status):
        for pool in queryset:
            services.set_pool_manual_status(pool, manual_status, actor=request.user)
        messages.success(request, f"Zmieniono {queryset.count()} pul na {manual_status}.")

    @admin.action(description="Wymuś otwarcie puli", permissions=["change"])
    def force_open(self, request, queryset):
        self._set_manual(request, queryset, PoolManualStatus.FORCED_OPEN)

    @admin.action(description="Wymuś zamknięcie puli", permissions=["change"])
    def force_close(self, request, queryset):
        self._set_manual(request, queryset, PoolManualStatus.FORCED_CLOSED)

    @admin.action(description="Przywróć tryb automatyczny", permissions=["change"])
    def set_auto(self, request, queryset):
        self._set_manual(request, queryset, PoolManualStatus.AUTO)
