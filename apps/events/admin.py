from datetime import datetime, time

from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from apps.admin_badges import choice_badge

from . import services
from .models import Event, EventStatus, PoolManualStatus, TicketPool

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


class TicketPoolInline(admin.TabularInline):
    model = TicketPool
    formset = TicketPoolFormSet
    extra = 1
    # currency is always PLN; manual_status (force open/closed) only makes
    # sense once a pool is live, not while it's still being created here —
    # it stays reachable from the standalone TicketPoolAdmin actions.
    exclude = ["currency", "manual_status"]

    def get_fields(self, request, obj=None):
        fields = ["name", "price_gross", "capacity", "sales_start_at", "sales_end_at", "on_sellout"]
        if obj is not None:
            # Meaningless while the event is still being created (always
            # zero) — sales figures belong in reports/the dashboard, not
            # here. Real once the event exists and pools can actually sell.
            fields.insert(3, "sold_count")
        return fields

    def get_readonly_fields(self, request, obj=None):
        return ["sold_count"] if obj is not None else []


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "start_at",
        "status",
        "sales_state",
        "capacity_total",
        "sold_total",
        "revenue_paid",
        "sales_start_at",
        "sales_end_at",
    ]
    list_filter = ["status"]
    search_fields = ["title", "slug"]
    readonly_fields = [
        "status",
        "sold_total",
        "revenue_paid",
        "calendar_event_id",
        "created_by",
        "updated_by",
        "created_at",
        "updated_at",
    ]
    date_hierarchy = "start_at"
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
                        else "Status zmienia się przez akcje „Opublikuj”/„Odwołaj” na "
                        "liście wydarzeń, nie tutaj."
                    ),
                    "fields": (
                        ["title", "max_tickets_per_order"]
                        if obj is None
                        else ["title", "status", "max_tickets_per_order"]
                    ),
                },
            ),
            (
                "Opis",
                {
                    "description": (
                        "Treść widoczna na publicznej stronie wydarzenia. Miejsce "
                        "(Podwórko.art / Scena UCHO, ul. Świętego Piotra 2, Gdynia) jest "
                        "takie samo dla każdego wydarzenia."
                    ),
                    "fields": ["description"],
                },
            ),
            (
                "Termin wydarzenia",
                {
                    "description": (
                        "Kalendarz poniżej zaznacza już zatwierdzone wydarzenia — "
                        "sprawdź kolizje przed wyborem daty. Koniec podaj tylko, jeśli "
                        "już wiadomo."
                    ),
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
        return super().change_view(request, object_id, form_url, extra_context)

    @admin.display(description="stan sprzedaży")
    def sales_state(self, obj):
        return choice_badge(services.get_sales_state(obj), SALES_STATE_TONES, SALES_STATE_LABELS)

    @admin.display(description="przychód (opłacone)")
    def revenue_paid(self, obj):
        from apps.orders.models import PaymentConfig
        from apps.reports.services import event_revenue_summary

        is_demo = PaymentConfig.is_demo_mode()
        return event_revenue_summary(obj, is_demo=is_demo)["paid"]

    @admin.action(description="Opublikuj wybrane wydarzenia")
    def publish_events(self, request, queryset):
        for event in queryset:
            try:
                services.publish_event(event, actor=request.user)
                messages.success(request, f"Opublikowano: {event}")
            except ValidationError as exc:
                messages.error(request, f"{event}: {'; '.join(exc.messages)}")

    @admin.action(description="Odwołaj wybrane wydarzenia")
    def cancel_events(self, request, queryset):
        for event in queryset:
            try:
                services.cancel_event(event, actor=request.user)
                messages.warning(request, f"Odwołano: {event}")
            except ValidationError as exc:
                messages.error(request, f"{event}: {'; '.join(exc.messages)}")

    @admin.action(description="Odwołaj i zwróć wszystkie opłacone zamówienia")
    def cancel_events_with_refunds(self, request, queryset):
        from apps.orders.refunds import cancel_event_with_refunds

        for event in queryset:
            try:
                summary = cancel_event_with_refunds(event, actor=request.user)
                messages.warning(
                    request,
                    f"Odwołano {event}: zwroty {summary['refunded']}, "
                    f"błędy {summary['failed']}, anulowane nieopłacone "
                    f"{summary['cancelled_unpaid']}.",
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
        ends = [p.sales_end_at for p in pools if p.sales_end_at]
        if starts and ends:
            event.sales_start_at = min(starts)
            event.sales_end_at = max(ends)
            update_fields += ["sales_start_at", "sales_end_at"]
        if pools:
            event.capacity_total = sum(p.capacity for p in pools)
            update_fields.append("capacity_total")
        if update_fields:
            event.save(update_fields=update_fields)

    def response_add(self, request, obj, post_url_continue=None):
        if "_publish" in request.POST:
            return self._publish_and_redirect(request, obj)
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
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
    list_display = [
        "name",
        "event",
        "sales_start_at",
        "sales_end_at",
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

    @admin.action(description="Wymuś otwarcie puli")
    def force_open(self, request, queryset):
        self._set_manual(request, queryset, PoolManualStatus.FORCED_OPEN)

    @admin.action(description="Wymuś zamknięcie puli")
    def force_close(self, request, queryset):
        self._set_manual(request, queryset, PoolManualStatus.FORCED_CLOSED)

    @admin.action(description="Przywróć tryb automatyczny")
    def set_auto(self, request, queryset):
        self._set_manual(request, queryset, PoolManualStatus.AUTO)
