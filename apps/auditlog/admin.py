from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "actor_label", "actor_role", "action", "entity_type", "entity_id"]
    list_filter = ["action", "entity_type", "actor_role"]
    search_fields = ["entity_id", "actor_label", "action"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
