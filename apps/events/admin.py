from django.contrib import admin

from .models import Event, TicketPool


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
    ]
    list_filter = ["manual_status", "event"]
    readonly_fields = ["sold_count"]
