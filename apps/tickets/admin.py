from django.contrib import admin

from .models import EmailOutbox, Ticket


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = [
        "short_code",
        "event",
        "buyer_email",
        "status",
        "is_demo",
        "issued_at",
        "checked_in_at",
    ]
    list_filter = ["status", "is_demo", "event"]
    search_fields = ["short_code", "buyer_email", "id"]
    readonly_fields = [
        "id",
        "event",
        "pool",
        "order",
        "buyer_email",
        "short_code",
        "qr_token_hash",
        "status",
        "is_demo",
        "issued_at",
        "checked_in_at",
        "checked_in_by",
        "refunded_at",
        "cancelled_at",
    ]

    def has_add_permission(self, request):
        return False  # tickets are issued only by the payment flow

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EmailOutbox)
class EmailOutboxAdmin(admin.ModelAdmin):
    list_display = ["created_at", "to_email", "subject", "status", "attempts", "sent_at"]
    list_filter = ["status"]
    search_fields = ["to_email", "subject"]
    readonly_fields = ["created_at", "sent_at", "attempts", "last_error"]
