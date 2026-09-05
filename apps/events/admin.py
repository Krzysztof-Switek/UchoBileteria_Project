from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from apps.admin_badges import choice_badge

from . import services
from .models import Event, PoolManualStatus, TicketPool

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


class TicketPoolInline(admin.TabularInline):
    model = TicketPool
    extra = 0
    readonly_fields = ["sold_count"]


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
    prepopulated_fields = {"slug": ["title"]}
    readonly_fields = [
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

    # Fields that only make sense once the event exists: live stats, system
    # integrations, and the lead-in to the ticket-pool inline below it. Kept
    # off the "add" form so a new event starts with just the essentials.
    EDIT_ONLY_FIELDSETS = [
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
        (
            "Pule biletowe",
            {
                "description": (
                    "Zarządzaj pulami poniżej — każda ma własną cenę, pojemność i "
                    "priorytet (niższa liczba = wcześniej w kolejce aktywacji). "
                    "Reguła aktywacji: pula włącza się automatycznie, gdy nadejdzie "
                    "jej „start sprzedaży puli” ALBO gdy poprzednia pula (niższy "
                    "priorytet) się wyprzeda — cokolwiek nastąpi pierwsze. Puste pole "
                    "startu sprzedaży = pula gotowa od razu, jeśli żadna wcześniejsza "
                    "pula jej nie blokuje."
                ),
                "fields": [],
            },
        ),
    ]

    def get_fieldsets(self, request, obj=None):
        fieldsets = [
            (
                "Podstawowe informacje",
                {
                    "description": (
                        "Nazwa i status wydarzenia. Adres strony (slug) tworzy się "
                        "automatycznie z tytułu."
                    ),
                    "fields": ["title", "slug", "status"],
                },
            ),
            (
                "Opis i miejsce",
                {
                    "description": "Treść i lokalizacja widoczne na publicznej stronie wydarzenia.",
                    "fields": ["description", "venue_name", "venue_address"],
                },
            ),
            (
                "Termin wydarzenia",
                {
                    "description": "Data i godziny samej imprezy.",
                    "fields": ["start_at", "end_at"],
                },
            ),
            (
                "Sprzedaż",
                {
                    "description": (
                        "Okno sprzedaży i limity. Pojemność to twardy limit miejsc na "
                        "całą imprezę — pule biletowe (dodawane po zapisaniu) mogą się "
                        "do niego zbliżać, ale sprzedaż nigdy go nie przekroczy."
                    ),
                    "fields": [
                        "sales_start_at",
                        "sales_end_at",
                        "max_tickets_per_order",
                        "capacity_total",
                    ],
                },
            ),
        ]
        if obj is not None:
            fieldsets += self.EDIT_ONLY_FIELDSETS
        return fieldsets

    def get_inline_instances(self, request, obj=None):
        # Pools belong to a saved event — nothing to show while creating one.
        if obj is None:
            return []
        return super().get_inline_instances(request, obj)

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
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        # Keep Google Calendar in sync when a published event is edited.
        if change and obj.status == "PUBLISHED":
            from .calendar import enqueue_calendar_sync
            from .models import CalendarAction

            enqueue_calendar_sync(obj, CalendarAction.UPSERT)


@admin.register(TicketPool)
class TicketPoolAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "event",
        "priority",
        "price_gross",
        "currency",
        "capacity",
        "sold_count",
        "manual_status",
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
