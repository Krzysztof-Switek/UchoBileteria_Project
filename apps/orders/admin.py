from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse

from apps.admin_badges import choice_badge
from apps.admin_badges import demo_badge as render_demo_badge

from . import refunds
from .models import Order, OrderStatus, PaymentConfig, PaymentEvent, PaymentEventStatus

ORDER_STATUS_TONES = {
    OrderStatus.CREATED: "info",
    OrderStatus.PAYMENT_PENDING: "warning",
    OrderStatus.PAID: "success",
    OrderStatus.FAILED: "danger",
    OrderStatus.EXPIRED: "neutral",
    OrderStatus.REFUNDED: "danger",
    OrderStatus.CANCELLED: "neutral",
}

PAYMENT_EVENT_STATUS_TONES = {
    PaymentEventStatus.RECEIVED: "info",
    PaymentEventStatus.PROCESSED: "success",
    PaymentEventStatus.DUPLICATE: "neutral",
    PaymentEventStatus.IGNORED: "neutral",
    PaymentEventStatus.ERROR: "danger",
}


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        "short_id",
        "event",
        "buyer_email",
        "quantity",
        "amount_gross",
        "currency",
        "status_badge",
        "demo_badge",
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
    actions = ["refund_selected_orders"]
    change_form_template = "admin/orders/order/change_form.html"

    @admin.display(description="status", ordering="status")
    def status_badge(self, obj):
        return choice_badge(obj.status, ORDER_STATUS_TONES, dict(OrderStatus.choices))

    @admin.display(description="tryb", ordering="is_demo")
    def demo_badge(self, obj):
        return render_demo_badge(obj.is_demo)

    def has_add_permission(self, request):
        return False  # orders are created only by the purchase flow

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        return [
            path(
                "<path:object_id>/zwrot/",
                self.admin_site.admin_view(self.refund_view),
                name="orders_order_refund",
            ),
        ] + super().get_urls()

    def refund_view(self, request, object_id):
        """OBS-03: refund as a deliberate, confirmed action for a single order."""
        order = get_object_or_404(Order, pk=object_id)
        if not self.has_change_permission(request, order):
            messages.error(request, "Brak uprawnień do zwrotu zamówienia.")
            return redirect(self._change_url(order))
        if order.status != OrderStatus.PAID:
            messages.error(request, "Zwrócić można tylko opłacone zamówienie.")
            return redirect(self._change_url(order))

        if request.method == "POST":
            reason = request.POST.get("reason", "").strip()
            try:
                refunds.refund_order(order, actor=request.user, reason=reason)
                messages.success(request, f"Zwrócono zamówienie {order.short_id}.")
            except ValidationError as exc:
                messages.error(request, f"{order.short_id}: {'; '.join(exc.messages)}")
            except Exception as exc:  # noqa: BLE001 - report provider failures
                messages.error(request, f"{order.short_id}: błąd zwrotu ({exc}).")
            return redirect(self._change_url(order))

        context = {
            **self.admin_site.each_context(request),
            "title": f"Potwierdź zwrot zamówienia {order.short_id}",
            "orders": [order],
            "total_amount": order.amount_gross,
            "total_tickets": order.quantity,
            "cancel_url": self._change_url(order),
            "opts": self.model._meta,
        }
        return render(request, "admin/orders/order/refund_confirm.html", context)

    def _change_url(self, order):
        return reverse("admin:orders_order_change", args=[order.pk])

    @admin.action(description="Zwróć zaznaczone zamówienia (pełny zwrot)")
    def refund_selected_orders(self, request, queryset):
        # OBS-03: the bulk action gets the same confirmation step as the
        # single-order button, instead of refunding on a single click.
        if request.POST.get("confirm_refund") == "yes":
            reason = request.POST.get("reason", "").strip()
            for order in queryset:
                try:
                    refunds.refund_order(order, actor=request.user, reason=reason)
                    messages.success(request, f"Zwrócono zamówienie {order.short_id}.")
                except ValidationError as exc:
                    messages.error(request, f"{order.short_id}: {'; '.join(exc.messages)}")
                except Exception as exc:  # noqa: BLE001 - report provider failures per order
                    messages.error(request, f"{order.short_id}: błąd zwrotu ({exc}).")
            return None

        not_paid = [o for o in queryset if o.status != OrderStatus.PAID]
        payable = [o for o in queryset if o.status == OrderStatus.PAID]
        if not_paid:
            messages.warning(
                request,
                "Pominięto (nieopłacone, nic do zwrotu): "
                + ", ".join(o.short_id for o in not_paid),
            )
        if not payable:
            messages.error(request, "Żadne z zaznaczonych zamówień nie jest opłacone.")
            return None

        context = {
            **self.admin_site.each_context(request),
            "title": "Potwierdź zwrot zaznaczonych zamówień",
            "orders": payable,
            "total_amount": sum((o.amount_gross for o in payable), start=0),
            "total_tickets": sum(o.quantity for o in payable),
            "action_checkbox_name": ACTION_CHECKBOX_NAME,
            "selected_ids": [o.pk for o in payable],
            "action_name": "refund_selected_orders",
            "cancel_url": reverse("admin:orders_order_changelist"),
            "opts": self.model._meta,
        }
        return render(request, "admin/orders/order/refund_confirm.html", context)


@admin.register(PaymentEvent)
class PaymentEventAdmin(admin.ModelAdmin):
    list_display = [
        "received_at",
        "provider",
        "provider_event_id",
        "event_type",
        "order",
        "status_badge",
        "demo_badge",
    ]
    list_filter = ["provider", "processing_status", "is_demo"]
    search_fields = ["provider_event_id"]

    @admin.display(description="status", ordering="processing_status")
    def status_badge(self, obj):
        return choice_badge(
            obj.processing_status, PAYMENT_EVENT_STATUS_TONES, dict(PaymentEventStatus.choices)
        )

    @admin.display(description="tryb", ordering="is_demo")
    def demo_badge(self, obj):
        return render_demo_badge(obj.is_demo)

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

    def save_model(self, request, obj, form, change):
        from django.conf import settings

        from apps.auditlog.services import log_action

        from .models import PaymentMode

        if obj.mode == PaymentMode.LIVE and not (
            settings.STRIPE_SECRET_KEY and settings.STRIPE_WEBHOOK_SECRET
        ):
            messages.error(
                request,
                "Nie można włączyć trybu LIVE: brak skonfigurowanych kluczy Stripe "
                "(STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET).",
            )
            return  # refuse the switch
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        log_action("payment_config.mode_changed", obj, actor=request.user,
                   metadata={"mode": obj.mode})
        messages.warning(request, f"Tryb płatności: {obj.get_mode_display()}.")
