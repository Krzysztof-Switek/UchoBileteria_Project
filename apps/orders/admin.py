from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone

from apps.admin_badges import choice_badge
from apps.admin_badges import demo_badge as render_demo_badge
from apps.admin_format import PerEventChangelistMixin, dt_column, short_dt

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


MONTHS_PL = ["styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec", "lipiec",
             "sierpień", "wrzesień", "październik", "listopad", "grudzień"]


@admin.register(Order)
class OrderAdmin(PerEventChangelistMixin, admin.ModelAdmin):
    event_lookup = "event__id__exact"
    list_label = "Zamówienia"
    created_short = dt_column("created_at", "utworzono")

    list_display = [
        "id_short",
        "event",
        "buyer_email",
        "quantity",
        "amount_gross",
        "currency",
        "status_badge",
        "demo_badge",
        "created_short",
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

    @admin.display(description="ID", ordering="id")
    def id_short(self, obj):
        return obj.short_id

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
            # Before super()'s "<path:object_id>/" catch-all.
            path(
                "wydarzenia/",
                self.admin_site.admin_view(self.by_event_view),
                name="orders_by_event",
            ),
            path(
                "<path:object_id>/zwrot/",
                self.admin_site.admin_view(self.refund_view),
                name="orders_order_refund",
            ),
        ] + super().get_urls()

    def by_event_view(self, request):
        """Entry point of the per-event tiles (orders, tickets, e-mails, logs):
        pick an event (search by name, date, month, quarter, year), then open
        its orders, tickets, e-mails, payment log or audit log."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        from apps.events.models import Event

        is_demo = PaymentConfig.is_demo_mode()
        params = request.GET
        events = Event.objects.all()
        q = params.get("q", "").strip()
        if q:
            events = events.filter(title__icontains=q)
        year, quarter, month = params.get("rok"), params.get("kwartal"), params.get("miesiac")
        if year and year.isdigit():
            events = events.filter(start_at__year=int(year))
        if quarter and quarter.isdigit() and 1 <= int(quarter) <= 4:
            first = 3 * (int(quarter) - 1) + 1
            events = events.filter(start_at__month__in=[first, first + 1, first + 2])
        if month and month.isdigit():
            events = events.filter(start_at__month=int(month))
        day = params.get("data", "")
        if day:
            events = events.filter(start_at__date=day)

        order_stats = {
            row["event"]: row
            for row in Order.objects.filter(is_demo=is_demo)
            .values("event")
            .annotate(
                orders=Count("id"),
                paid=Sum("amount_gross", filter=Q(status=OrderStatus.PAID)),
            )
        }
        from apps.tickets.models import Ticket, TicketStatus

        ticket_stats = {
            row["event"]: row
            for row in Ticket.objects.filter(is_demo=is_demo)
            .values("event")
            .annotate(
                issued=Count("id"),
                checked_in=Count("id", filter=Q(status=TicketStatus.CHECKED_IN)),
            )
        }
        from apps.auditlog.models import AuditLog
        from apps.tickets.models import EmailOutbox, EmailStatus

        email_stats = {
            row["order__event"]: row
            for row in EmailOutbox.objects.filter(order__is_demo=is_demo)
            .values("order__event")
            .annotate(
                total=Count("id"),
                failed=Count("id", filter=Q(status=EmailStatus.FAILED)),
            )
        }

        audit_counts = dict(
            AuditLog.objects.filter(event__isnull=False)
            .values_list("event")
            .annotate(n=Count("id"))
        )
        log_counts = dict(
            PaymentEvent.objects.filter(is_demo=is_demo, order__isnull=False)
            .values_list("order__event")
            .annotate(n=Count("id"))
        )
        show_empty = params.get("puste") == "1"
        rows = []
        for event in events.order_by("-start_at"):
            stats = order_stats.get(event.pk, {})
            # "Activity" = orders or audit log entries (a published event with
            # no sales yet still has its "published" entry worth seeing).
            if not stats and not audit_counts.get(event.pk) and not show_empty:
                continue
            rows.append({
                "event": event,
                "start": short_dt(event.start_at),
                "orders": stats.get("orders", 0),
                "paid": stats.get("paid") or 0,
                "log": log_counts.get(event.pk, 0),
                "tickets": ticket_stats.get(event.pk, {}).get("issued", 0),
                "checked_in": ticket_stats.get(event.pk, {}).get("checked_in", 0),
                "audit": audit_counts.get(event.pk, 0),
                "emails": email_stats.get(event.pk, {}).get("total", 0),
                "emails_failed": email_stats.get(event.pk, {}).get("failed", 0),
            })

        years = sorted({d.year for d in Event.objects.dates("start_at", "year")}, reverse=True)
        context = {
            **self.admin_site.each_context(request),
            "title": "Wybierz wydarzenie",
            "opts": self.model._meta,
            "rows": rows,
            "params": params,
            "years": years,
            "months": list(enumerate(MONTHS_PL, start=1)),
            "quarters": [1, 2, 3, 4],
            "show_empty": show_empty,
            "is_demo": is_demo,
            "unassigned_log": PaymentEvent.objects.filter(
                is_demo=is_demo, order__isnull=True
            ).count(),
            "unassigned_audit": AuditLog.objects.filter(event__isnull=True).count(),
            "unassigned_emails": EmailOutbox.objects.filter(order__isnull=True).count(),
            "today": timezone.localdate(),
        }
        return render(request, "admin/orders/order/by_event.html", context)

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

    @admin.action(description="Zwróć zaznaczone zamówienia (pełny zwrot)",
                  permissions=["change"])
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
class PaymentEventAdmin(PerEventChangelistMixin, admin.ModelAdmin):
    event_lookup = "order__event__id__exact"
    event_attr = "order.event"
    list_label = "Dziennik płatności"
    received_short = dt_column("received_at", "otrzymano")

    def lookup_allowed(self, lookup, value, request=None):
        # Per-event view (and "entries without an order" from the picker).
        if lookup in ("order__event__id__exact", "order__isnull"):
            return True
        return super().lookup_allowed(lookup, value, request)

    list_display = [
        "received_short",
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

    # The DEMO/LIVE switch is for the superuser only — hidden from every role
    # (ADMIN group and MANAGER included), not just read-only for them.
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser and not PaymentConfig.objects.exists()

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
