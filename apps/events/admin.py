from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from . import services
from .models import Event, PoolManualStatus, TicketPool


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
        "sales_start_at",
        "sales_end_at",
    ]
    list_filter = ["status"]
    search_fields = ["title", "slug"]
    prepopulated_fields = {"slug": ["title"]}
    readonly_fields = ["sold_total", "calendar_event_id", "created_at", "updated_at"]
    date_hierarchy = "start_at"
    inlines = [TicketPoolInline]
    actions = ["publish_events", "cancel_events", "cancel_events_with_refunds"]

    @admin.display(description="stan sprzedaży")
    def sales_state(self, obj):
        return services.get_sales_state(obj)

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
