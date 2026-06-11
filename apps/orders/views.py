from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.events.models import Event, EventStatus

from . import services
from .forms import PurchaseForm
from .models import Order
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

    return redirect("orders:detail", order_id=order.id)


def order_detail(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    return render(request, "orders/detail.html", {"order": order})
