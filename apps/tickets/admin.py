from django.contrib import admin

from apps.admin_badges import choice_badge
from apps.admin_badges import demo_badge as render_demo_badge

from .models import EmailOutbox, EmailStatus, Ticket, TicketStatus

TICKET_STATUS_TONES = {
    TicketStatus.ISSUED: "success",
    TicketStatus.CHECKED_IN: "info",
    TicketStatus.REFUNDED: "danger",
    TicketStatus.CANCELLED: "neutral",
    TicketStatus.INVALIDATED: "warning",
}

EMAIL_STATUS_TONES = {
    EmailStatus.PENDING: "warning",
    EmailStatus.SENT: "success",
    EmailStatus.FAILED: "danger",
}


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = [
        "short_code",
        "event",
        "buyer_email",
        "status_badge",
        "demo_badge",
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

    @admin.display(description="status", ordering="status")
    def status_badge(self, obj):
        return choice_badge(obj.status, TICKET_STATUS_TONES, dict(TicketStatus.choices))

    @admin.display(description="tryb", ordering="is_demo")
    def demo_badge(self, obj):
        return render_demo_badge(obj.is_demo)

    def has_add_permission(self, request):
        return False  # tickets are issued only by the payment flow

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EmailOutbox)
class EmailOutboxAdmin(admin.ModelAdmin):
    list_display = ["created_at", "to_email", "subject", "status_badge", "attempts", "sent_at"]
    list_filter = ["status"]
    search_fields = ["to_email", "subject"]
    readonly_fields = ["created_at", "sent_at", "attempts", "last_error"]

    @admin.display(description="status", ordering="status")
    def status_badge(self, obj):
        return choice_badge(obj.status, EMAIL_STATUS_TONES, dict(EmailStatus.choices))
