from django.contrib import admin

from apps.admin_format import PerEventChangelistMixin, dt_column

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(PerEventChangelistMixin, admin.ModelAdmin):
    event_lookup = "event__id__exact"
    list_label = "Dziennik zdarzeń"
    created_short = dt_column("created_at", "kiedy")
    list_display = [
        "created_short", "actor_label", "actor_role", "action", "event", "entity_type",
        "entity_id",
    ]
    list_filter = ["action", "entity_type", "actor_role"]
    search_fields = ["entity_id", "actor_label", "action"]
    date_hierarchy = "created_at"

    def lookup_allowed(self, lookup, value, request=None):
        # Per-event view, and "entries not tied to any event" from the picker.
        if lookup in ("event__id__exact", "event__isnull"):
            return True
        return super().lookup_allowed(lookup, value, request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
