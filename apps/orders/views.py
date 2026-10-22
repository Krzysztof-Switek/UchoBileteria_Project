import json
from urllib.parse import urlencode

from django.contrib import messages
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.events.models import Event, EventStatus

from . import services
from .forms import PurchaseForm, SeatPurchaseForm
from .models import Order, OrderStatus, PaymentProviderKind
from .payments import SignatureError
from .providers import demo as demo_provider
from .providers import get_provider
from .ratelimit import is_rate_limited, resend_is_cooling_down
from .terms import TERMS_VERSION


@require_POST
def purchase(request, slug):
    event = get_object_or_404(Event, slug=slug, status=EventStatus.PUBLISHED)

    if is_rate_limited(request):
        messages.error(request, "Zbyt wiele prób zakupu. Spróbuj ponownie za kilka minut.")
        return redirect("events:detail", slug=event.slug)

    if event.is_seated:
        return _purchase_seats(request, event)

    form = PurchaseForm(request.POST, max_quantity=event.max_tickets_per_order)
    if not form.is_valid():
        if "accept_terms" in form.errors:
            messages.error(request, form.errors["accept_terms"][0])
        else:
            messages.error(request, "Sprawdź poprawność adresu e-mail i liczby biletów.")
        return redirect("events:detail", slug=event.slug)

    try:
        order = services.create_order(
            event,
            buyer_email=form.cleaned_data["buyer_email"],
            quantity=form.cleaned_data["quantity"],
            terms_version=TERMS_VERSION,
        )
    except services.PurchaseError as exc:
        messages.error(request, str(exc))
        return redirect("events:detail", slug=event.slug)

    checkout_url = get_provider(order).start_checkout(order)
    return redirect(checkout_url)


def _back_to_map(event, seats=(), email=""):
    """Event page with the buyer's selection restored (minus lost seats)."""
    url = reverse("events:detail", args=[event.slug])
    query = {}
    if seats:
        query["wybrane"] = ",".join(seats)
    if email:
        query["email"] = email
    return redirect(url + ("?" + urlencode(query) if query else "") + "#mapa")


def _purchase_seats(request, event):
    form = SeatPurchaseForm(request.POST)
    if not form.is_valid():
        if "accept_terms" in form.errors:
            messages.error(request, form.errors["accept_terms"][0])
        else:
            messages.error(request, "Podaj poprawny e-mail i wybierz miejsca na mapie.")
        seats = [k for k in request.POST.get("seats", "").split(",") if k.strip()]
        return _back_to_map(event, seats, request.POST.get("buyer_email", ""))
    email = form.cleaned_data["buyer_email"]
    try:
        order = services.create_order(event, buyer_email=email,
                                      seat_keys=form.cleaned_data["seats"],
                                      terms_version=TERMS_VERSION)
    except services.SeatConflictError as exc:
        messages.error(request, str(exc))
        return _back_to_map(event, exc.still_free, email)
    except services.PurchaseError as exc:
        messages.error(request, str(exc))
        return _back_to_map(event, form.cleaned_data["seats"], email)
    checkout_url = get_provider(order).start_checkout(order)
    return redirect(checkout_url)


def change_seats(request, order_id):
    """Buyer swaps the seats of an unpaid order (same number) before paying."""
    order = get_object_or_404(Order, id=order_id)
    event = order.event
    pending = order.status in (OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING)
    if not event.is_seated or not order.seat_keys or not pending:
        messages.error(request, "W tym zamówieniu nie można zmienić miejsc.")
        return redirect("orders:detail", order_id=order.id)
    if request.method == "POST":
        keys = [k.strip() for k in request.POST.get("seats", "").split(",") if k.strip()]
        try:
            services.change_pending_order_seats(order, keys)
        except services.PurchaseError as exc:
            messages.error(request, str(exc))
            return redirect("orders:change_seats", order_id=order.id)
        messages.success(request, "Zmieniono miejsca: " + ", ".join(order.seat_labels) + ".")
        return redirect("orders:detail", order_id=order.id)

    from apps.seating.services import seat_map_payload, zone_prices

    return render(request, "orders/change_seats.html", {
        "order": order,
        "event": event,
        "seat_config": {
            **seat_map_payload(event),
            "mine": order.seat_keys,
            "price": str(order.pool.price_gross),
            "currency": order.currency,
            "states_url": reverse("events:seat_states", args=[event.slug])
            + f"?zamowienie={order.id}",
            "limit_count": order.quantity,
            "zone_prices": zone_prices(event, order.pool.price_gross),
        },
    })


def order_detail(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    checkout_url = None
    if order.status in (OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING):
        checkout_url = get_provider(order).start_checkout(order)
    return render(request, "orders/detail.html", {"order": order, "checkout_url": checkout_url})


def cancellation_choice(request, order_id):
    """Event cancelled with a new date: the buyer keeps the tickets for the new
    date or takes the refund (link from the e-mail; the order UUID is the key)."""
    from django.core.exceptions import ValidationError

    from . import cancellation
    from .models import CancellationStatus

    order = get_object_or_404(Order.objects.select_related("event"), id=order_id)
    event = order.moved_from_event or order.event
    if request.method == "POST" and order.cancellation_status == CancellationStatus.AWAITING:
        choice = request.POST.get("wybor")
        try:
            if choice == "nowy-termin":
                cancellation.move_to_replacement(order)
                messages.success(request, "Bilety są ważne na nowy termin — wysłaliśmy je "
                                          "ponownie e-mailem.")
            elif choice == "zwrot":
                if cancellation.choose_refund(order):
                    messages.success(request, "Zlecono zwrot pieniędzy na rachunek lub kartę, "
                                              "z której zapłacono.")
                else:
                    messages.warning(request, "Nie udało się automatycznie zlecić zwrotu — "
                                              "obsługa klubu skontaktuje się z Tobą e-mailem.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("orders:cancellation_choice", order_id=order.pk)
    order.refresh_from_db()
    return render(request, "orders/cancellation_choice.html", {
        "order": order, "event": event, "replacement": event.replacement_event,
    })


@require_POST
def resend_tickets(request, order_id):
    """KUP-03: let a buyer re-trigger their own ticket e-mail without
    calling the club — the most common support request per the audit."""
    order = get_object_or_404(Order, id=order_id)
    if order.status != OrderStatus.PAID or not order.tickets.exists():
        messages.error(request, "Dla tego zamówienia nie ma jeszcze biletów do wysłania.")
        return redirect("orders:detail", order_id=order.id)

    if resend_is_cooling_down(order.id):
        messages.warning(request, "E-mail z biletami został już wysłany przed chwilą.")
        return redirect("orders:detail", order_id=order.id)

    from apps.tickets.services import queue_ticket_email

    queue_ticket_email(order, list(order.tickets.all()))
    messages.success(request, f"Bilety zostały ponownie wysłane na {order.buyer_email}.")
    return redirect("orders:detail", order_id=order.id)


def tickets_pdf(request, order_id):
    """KUP-03: a self-sufficient PDF download for a paid order's tickets."""
    order = get_object_or_404(Order, id=order_id)
    if order.status != OrderStatus.PAID or not order.tickets.exists():
        messages.error(request, "Dla tego zamówienia nie ma jeszcze biletów do pobrania.")
        return redirect("orders:detail", order_id=order.id)

    from apps.tickets.services import generate_tickets_pdf

    pdf_bytes = generate_tickets_pdf(order)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="bilety-{order.short_id}.pdf"'
    return response


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
def stripe_webhook(request):
    """Stripe webhook endpoint: verify signature, normalize, process."""
    import stripe
    from django.conf import settings

    from .providers.stripe_provider import handle_stripe_event

    try:
        event = stripe.Webhook.construct_event(
            request.body,
            request.headers.get("Stripe-Signature", ""),
            settings.STRIPE_WEBHOOK_SECRET,
        )
    except Exception:  # noqa: BLE001 - any verification problem means reject
        return HttpResponse(status=400)
    handle_stripe_event(event)
    return HttpResponse("ok")


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
