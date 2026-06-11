import json

from django.contrib import messages
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.events.models import Event, EventStatus

from . import services
from .forms import PurchaseForm
from .models import Order, OrderStatus, PaymentProviderKind
from .payments import SignatureError
from .providers import demo as demo_provider
from .providers import get_provider
from .ratelimit import is_rate_limited


@require_POST
def purchase(request, slug):
    event = get_object_or_404(Event, slug=slug, status=EventStatus.PUBLISHED)

    if is_rate_limited(request):
        messages.error(request, "Zbyt wiele prób zakupu. Spróbuj ponownie za kilka minut.")
        return redirect("events:detail", slug=event.slug)

    form = PurchaseForm(request.POST, max_quantity=event.max_tickets_per_order)
    if not form.is_valid():
        messages.error(request, "Sprawdź poprawność adresu e-mail i liczby biletów.")
        return redirect("events:detail", slug=event.slug)

    try:
        order = services.create_order(
            event,
            buyer_email=form.cleaned_data["buyer_email"],
            quantity=form.cleaned_data["quantity"],
        )
    except services.PurchaseError as exc:
        messages.error(request, str(exc))
        return redirect("events:detail", slug=event.slug)

    checkout_url = get_provider(order).start_checkout(order)
    return redirect(checkout_url)


def order_detail(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    checkout_url = None
    if order.status in (OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING):
        checkout_url = get_provider(order).start_checkout(order)
    return render(request, "orders/detail.html", {"order": order, "checkout_url": checkout_url})


# --- Demo checkout (virtual currency) -------------------------------------


def _get_pending_demo_order(order_id) -> Order:
    return get_object_or_404(
        Order,
        id=order_id,
        payment_provider=PaymentProviderKind.DEMO,
        status=OrderStatus.PAYMENT_PENDING,
    )


def demo_checkout(request, order_id):
    order = get_object_or_404(Order, id=order_id, payment_provider=PaymentProviderKind.DEMO)
    if order.status != OrderStatus.PAYMENT_PENDING:
        return redirect("orders:detail", order_id=order.id)
    return render(request, "orders/demo_checkout.html", {"order": order})


@require_POST
def demo_pay(request, order_id):
    order = _get_pending_demo_order(order_id)
    body = demo_provider.build_event_body("payment.succeeded", order)
    demo_provider.deliver(body, demo_provider.sign(body))
    messages.success(request, "Płatność demo zakończona powodzeniem.")
    return redirect("orders:detail", order_id=order.id)


@require_POST
def demo_decline(request, order_id):
    order = _get_pending_demo_order(order_id)
    body = demo_provider.build_event_body("payment.failed", order)
    demo_provider.deliver(body, demo_provider.sign(body))
    messages.error(request, "Płatność demo odrzucona (symulacja).")
    return redirect("orders:detail", order_id=order.id)


@require_POST
def demo_pay_delayed(request, order_id):
    """
    Simulates "the buyer paid but the webhook is delayed" — the payload is
    shown on screen and delivered only when the tester clicks the button
    (possibly after the order has already expired).
    """
    order = _get_pending_demo_order(order_id)
    body = demo_provider.build_event_body("payment.succeeded", order)
    context = {
        "order": order,
        "payload": body.decode(),
        "signature": demo_provider.sign(body),
    }
    return render(request, "orders/demo_delayed.html", context)


@require_POST
def demo_deliver_webhook(request):
    """Deliver a previously generated (delayed) demo webhook."""
    payload = request.POST.get("payload", "")
    signature = request.POST.get("signature", "")
    try:
        demo_provider.deliver(payload.encode(), signature)
    except (SignatureError, json.JSONDecodeError):
        return HttpResponseBadRequest("Nieprawidłowy webhook.")
    order_id = json.loads(payload)["order_id"]
    messages.info(request, "Webhook demo dostarczony.")
    return redirect("orders:detail", order_id=order_id)


@require_POST
def demo_abandon(request, order_id):
    order = _get_pending_demo_order(order_id)
    services.cancel_unpaid_order(order)
    messages.info(request, "Zakup anulowany — rezerwacja została zwolniona.")
    return redirect("events:detail", slug=order.event.slug)


@csrf_exempt
@require_POST
def demo_webhook(request):
    """HTTP webhook endpoint — same pipeline as the in-process delivery."""
    signature = request.headers.get("X-Demo-Signature", "")
    try:
        demo_provider.deliver(request.body, signature)
    except SignatureError:
        return HttpResponse(status=400)
    except (json.JSONDecodeError, KeyError):
        return HttpResponseBadRequest("Malformed payload.")
    return HttpResponse("ok")
