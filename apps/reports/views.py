import csv

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.shortcuts import render

from . import services


def _is_demo(request) -> bool:
    """Reports default to demo data until the live switch is flipped."""
    mode = request.GET.get("tryb")
    if mode in ("demo", "live"):
        return mode == "demo"
    from apps.orders.models import PaymentConfig

    return PaymentConfig.is_demo_mode()


@staff_member_required
def dashboard(request):
    is_demo = _is_demo(request)
    sales = services.event_sales_report(is_demo=is_demo)
    flows = services.cash_flow_report(is_demo=is_demo)
    context = {
        "is_demo": is_demo,
        "sales": sales,
        "flows": flows,
        "totals": services.cash_flow_totals(flows),
        "currency": "DEMO" if is_demo else "PLN",
    }
    return render(request, "reports/dashboard.html", context)


@staff_member_required
def sales_csv(request):
    is_demo = _is_demo(request)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="sprzedaz.csv"'
    response.write("﻿")
    writer = csv.writer(response)
    writer.writerow(
        ["Wydarzenie", "Data", "Zam. opłacone", "Zam. zwrócone", "Bilety ważne",
         "Wykorzystane", "Zwrócone", "Przychód", "Zwroty", "Tryb"]
    )
    for row in services.event_sales_report(is_demo=is_demo):
        writer.writerow(
            [
                row["event"].title,
                row["event"].start_at.strftime("%Y-%m-%d"),
                row["orders_paid"],
                row["orders_refunded"],
                row["issued"],
                row["checked_in"],
                row["refunded"],
                row["revenue_paid"],
                row["revenue_refunded"],
                "DEMO" if is_demo else "LIVE",
            ]
        )
    return response


@staff_member_required
def cash_flow_csv(request):
    is_demo = _is_demo(request)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="przeplywy.csv"'
    response.write("﻿")
    writer = csv.writer(response)
    writer.writerow(["Dzień", "Wpłaty", "Zwroty", "Saldo", "Tryb"])
    for row in services.cash_flow_report(is_demo=is_demo):
        writer.writerow(
            [row["day"], row["inflow"], row["outflow"], row["net"],
             "DEMO" if is_demo else "LIVE"]
        )
    return response
