from django.contrib import admin

from apps.admin_badges import choice_badge
from apps.admin_badges import demo_badge as render_demo_badge
from apps.admin_format import PerEventChangelistMixin, dt_column

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
class TicketAdmin(PerEventChangelistMixin, admin.ModelAdmin):
    event_lookup = "event__id__exact"
    list_label = "Bilety"
    issued_short = dt_column("issued_at", "wydano")
    checked_in_short = dt_column("checked_in_at", "wejście")
    list_display = [
        "short_code",
        "event",
        "buyer_email",
        "status_badge",
        "demo_badge",
        "issued_short",
        "checked_in_short",
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
class EmailOutboxAdmin(PerEventChangelistMixin, admin.ModelAdmin):
    # E-mails reach their event through the order (tickets, refund notices).
    event_lookup = "order__event__id__exact"
    event_attr = "order.event"
    list_label = "E-maile"
    created_short = dt_column("created_at", "utworzono")
    sent_short = dt_column("sent_at", "wysłano")
    list_display = [
        "created_short", "to_email", "subject", "event_title", "status_badge", "attempts",
        "sent_short",
    ]
    list_filter = ["status"]
    search_fields = ["to_email", "subject"]
    readonly_fields = ["created_at", "sent_at", "attempts", "last_error"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("order__event")

    def lookup_allowed(self, lookup, value, request=None):
        # Per-event view, and "e-mails without an order" from the picker.
        if lookup in ("order__event__id__exact", "order__isnull"):
            return True
        return super().lookup_allowed(lookup, value, request)

    @admin.display(description="wydarzenie", ordering="order__event__title")
    def event_title(self, obj):
        return obj.order.event.title if obj.order_id else "—"

    @admin.display(description="status", ordering="status")
    def status_badge(self, obj):
        return choice_badge(obj.status, EMAIL_STATUS_TONES, dict(EmailStatus.choices))
