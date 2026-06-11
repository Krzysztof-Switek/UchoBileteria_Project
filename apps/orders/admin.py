from django.contrib import admin

from .models import Order, PaymentConfig, PaymentEvent


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        "short_id",
        "event",
        "buyer_email",
        "quantity",
        "amount_gross",
        "currency",
        "status",
        "is_demo",
        "created_at",
    ]
    list_filter = ["status", "is_demo", "payment_provider", "event"]
    search_fields = ["id", "buyer_email"]
    readonly_fields = [
        "id",
        "event",
        "pool",
        "quantity",
        "amount_gross",
        "currency",
        "status",
        "payment_provider",
        "payment_session_id",
        "provider_order_id",
        "is_demo",
        "created_at",
        "paid_at",
        "refunded_at",
    ]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False  # orders are created only by the purchase flow

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentEvent)
class PaymentEventAdmin(admin.ModelAdmin):
    list_display = [
        "received_at",
        "provider",
        "provider_event_id",
        "event_type",
        "order",
        "processing_status",
        "is_demo",
    ]
    list_filter = ["provider", "processing_status", "is_demo"]
    search_fields = ["provider_event_id"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentConfig)
class PaymentConfigAdmin(admin.ModelAdmin):
    list_display = ["mode", "updated_at", "updated_by"]

    def has_add_permission(self, request):
        return not PaymentConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
