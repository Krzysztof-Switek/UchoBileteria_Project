"""OBS-01: the admin landing page becomes an operations dashboard instead of
a bare model list. Inclusion tags (not a custom AdminSite.index override)
keep this decoupled from Django's own admin index view."""

from django import template
from django.utils import timezone

from apps.events.models import Event, EventStatus
from apps.orders.models import Order, OrderStatus, PaymentConfig, PaymentEvent, PaymentEventStatus
from apps.tickets.models import EmailOutbox, EmailStatus

register = template.Library()


@register.inclusion_tag("reports/_nearest_event_card.html")
def render_nearest_event_card():
    nearest_event = (
        Event.objects.filter(status=EventStatus.PUBLISHED, start_at__gte=timezone.now())
        .order_by("start_at")
        .first()
    )
    fill_pct = None
    fill_pct_str = None
    if nearest_event and nearest_event.capacity_total:
        fill_pct = round(100 * nearest_event.sold_total / nearest_event.capacity_total, 1)
        # Locale-safe width for the inline CSS style below — {{ fill_pct }}
        # alone would render with a Polish decimal comma ("0,8%"), which is
        # invalid CSS and silently makes the bar ignore its width entirely.
        fill_pct_str = f"{fill_pct:.1f}"

    return {
        "nearest_event": nearest_event,
        "fill_pct": fill_pct,
        "fill_pct_str": fill_pct_str,
    }


@register.inclusion_tag("reports/_attention_card.html")
def render_attention_card():
    is_demo = PaymentConfig.is_demo_mode()

    attention = {
        "payment_pending": Order.objects.filter(
            is_demo=is_demo, status=OrderStatus.PAYMENT_PENDING
        ).count(),
        "failed_webhooks": PaymentEvent.objects.filter(
            is_demo=is_demo, processing_status=PaymentEventStatus.ERROR
        ).count(),
        "failed_emails": EmailOutbox.objects.filter(status=EmailStatus.FAILED).count(),
    }

    return {
        "attention": attention,
        "has_attention": any(attention.values()),
    }
