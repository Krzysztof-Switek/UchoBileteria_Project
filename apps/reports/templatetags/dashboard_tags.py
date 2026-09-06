"""OBS-01: the admin landing page becomes an operations dashboard instead of
a bare model list. Inclusion tags (not a custom AdminSite.index override)
keep this decoupled from Django's own admin index view."""

from django import template

from apps.admin_badges import badge
from apps.events import services
from apps.events.admin import SALES_STATE_LABELS, SALES_STATE_TONES
from apps.events.models import Event, EventStatus
from apps.orders.models import Order, OrderStatus, PaymentConfig, PaymentEvent, PaymentEventStatus
from apps.tickets.models import EmailOutbox, EmailStatus

register = template.Library()

# Sales-state tones/labels (EventAdmin.sales_state) plus the two statuses
# that never go through get_sales_state: a draft has no sales state yet, a
# finished event's sales state no longer matters.
_STATUS_TONES = {**SALES_STATE_TONES, "DRAFT": "neutral", "FINISHED": "neutral"}
_STATUS_LABELS = {**SALES_STATE_LABELS, "DRAFT": "Szkic", "FINISHED": "Zakończone"}


def _event_row(event):
    if event.status == EventStatus.PUBLISHED:
        state = services.get_sales_state(event)
    else:
        state = event.status
    fill_pct = None
    if event.status == EventStatus.PUBLISHED and event.capacity_total:
        fill_pct = round(100 * event.sold_total / event.capacity_total, 1)
    return {
        "event": event,
        "badge": badge(_STATUS_LABELS.get(state, state), _STATUS_TONES.get(state, "neutral")),
        "fill_pct": fill_pct,
        # Locale-safe width for the inline CSS style below — {{ fill_pct }}
        # alone would render with a Polish decimal comma ("0,8%"), which is
        # invalid CSS and silently makes the bar ignore its width entirely.
        "fill_pct_str": f"{fill_pct:.1f}" if fill_pct is not None else None,
    }


@register.inclusion_tag("reports/_events_overview.html", takes_context=True)
def render_events_overview(context):
    show_finished = context["request"].GET.get("wydarzenia") == "zakonczone"
    if show_finished:
        qs = Event.objects.filter(status=EventStatus.FINISHED).order_by("-start_at")
    else:
        qs = Event.objects.filter(status__in=[EventStatus.DRAFT, EventStatus.PUBLISHED]).order_by(
            "start_at"
        )
    return {
        "rows": [_event_row(event) for event in qs[:15]],
        "show_finished": show_finished,
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
