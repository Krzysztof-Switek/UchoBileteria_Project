import csv
from datetime import date

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from openpyxl import Workbook
from openpyxl.styles import Font

from apps.events.models import Event

from . import services

# Reports show financial data: staff panel access + order viewing rights.
can_view_reports = permission_required("orders.view_order", raise_exception=True)

SALES_HEADER = [
    "Wydarzenie", "Data", "Zam. opłacone", "Zam. zwrócone", "Bilety ważne",
    "Wykorzystane", "Zwrócone", "Przychód", "Zwroty", "Tryb",
]
POOL_HEADER = [
    "Wydarzenie", "Pula", "Zam. opłacone", "Zam. zwrócone", "Bilety ważne",
    "Wykorzystane", "Zwrócone", "Przychód", "Zwroty", "Tryb",
]
FLOW_HEADER = ["Dzień", "Wpłaty", "Zwroty", "Saldo", "Tryb"]


def _is_demo(request) -> bool:
    """Reports default to demo data until the live switch is flipped."""
    mode = request.GET.get("tryb")
    if mode in ("demo", "live"):
        return mode == "demo"
    from apps.orders.models import PaymentConfig

    return PaymentConfig.is_demo_mode()


def _date_param(request, name: str) -> date | None:
    raw = request.GET.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _date_range(request) -> tuple[date | None, date | None]:
    okres = request.GET.get("okres")
    if okres:
        resolved = services.resolve_period(okres)
        if resolved:
            return resolved
    return _date_param(request, "od"), _date_param(request, "do")


def _date_qs(request) -> str:
    """Query-string suffix that carries the active period forward into CSV
    links and the DEMO/LIVE toggle — either the one-click ?okres= or a custom
    ?od=&do= range, whichever the current request actually used."""
    okres = request.GET.get("okres")
    if okres:
        return f"&okres={okres}"
    date_from, date_to = _date_param(request, "od"), _date_param(request, "do")
    qs = ""
    if date_from:
        qs += f"&od={date_from.isoformat()}"
    if date_to:
        qs += f"&do={date_to.isoformat()}"
    return qs


def _period_label(request, date_from: date | None, date_to: date | None) -> str:
    """RAP-06: short label for export filenames — e.g. "2026-Q3", "2026-08",
    "2026", an explicit date range, or "wszystko" for no filter at all."""
    okres = request.GET.get("okres")
    if date_from and okres in ("ten_miesiac", "poprzedni_miesiac"):
        return date_from.strftime("%Y-%m")
    if date_from and okres in ("ten_kwartal", "poprzedni_kwartal"):
        quarter = (date_from.month - 1) // 3 + 1
        return f"{date_from.year}-Q{quarter}"
    if date_from and okres == "ten_rok":
        return str(date_from.year)
    if date_from and date_to:
        if date_from == date_to:
            return date_from.isoformat()
        return f"{date_from.isoformat()}_{date_to.isoformat()}"
    return "wszystko"


def _export_filename(
    base: str, request, date_from: date | None, date_to: date | None, is_demo: bool, ext: str
) -> str:
    tryb = "DEMO" if is_demo else "LIVE"
    return f"{base}_{_period_label(request, date_from, date_to)}_{tryb}.{ext}"


def _csv_response(filename: str, header: list[str], rows) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("﻿")
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response


def _xlsx_response(filename: str, header: list[str], rows) -> HttpResponse:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(header)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append(row)
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    workbook.save(response)
    return response


def _sales_rows(*, is_demo: bool, date_from, date_to):
    for row in services.event_sales_report(is_demo=is_demo, date_from=date_from, date_to=date_to):
        yield [
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


def _pool_rows(*, is_demo: bool, date_from, date_to):
    for row in services.pool_sales_report(is_demo=is_demo, date_from=date_from, date_to=date_to):
        yield [
            row["event"].title,
            row["pool"].name,
            row["orders_paid"],
            row["orders_refunded"],
            row["issued"],
            row["checked_in"],
            row["refunded"],
            row["revenue_paid"],
            row["revenue_refunded"],
            "DEMO" if is_demo else "LIVE",
        ]


def _flow_rows(*, is_demo: bool, date_from, date_to):
    for row in services.cash_flow_report(is_demo=is_demo, date_from=date_from, date_to=date_to):
        yield [row["day"], row["inflow"], row["outflow"], row["net"], "DEMO" if is_demo else "LIVE"]


@staff_member_required
@can_view_reports
def dashboard(request):
    is_demo = _is_demo(request)
    date_from, date_to = _date_range(request)
    sales = services.event_sales_report(is_demo=is_demo, date_from=date_from, date_to=date_to)
    pools = services.pool_sales_report(is_demo=is_demo, date_from=date_from, date_to=date_to)
    flows = services.cash_flow_report(is_demo=is_demo, date_from=date_from, date_to=date_to)

    kpis, kpi_changes = None, None
    if date_from and date_to:
        kpis = services.period_kpis(is_demo=is_demo, date_from=date_from, date_to=date_to)
        prev_from, prev_to = services.previous_period(date_from, date_to)
        kpis_prev = services.period_kpis(is_demo=is_demo, date_from=prev_from, date_to=prev_to)
        kpi_changes = {key: services.percent_change(kpis[key], kpis_prev[key]) for key in kpis}

    chart_series = services.cash_flow_chart_series(
        is_demo=is_demo, date_from=date_from, date_to=date_to
    )

    context = {
        "is_demo": is_demo,
        "sales": sales,
        "pools": pools,
        "flows": flows,
        "totals": services.cash_flow_totals(flows),
        "currency": "DEMO" if is_demo else "PLN",
        "date_from": date_from,
        "date_to": date_to,
        "date_qs": _date_qs(request),
        "okres": request.GET.get("okres", ""),
        "period_choices": services.PERIOD_CHOICES,
        "sales_totals": services.sales_totals(sales),
        "pools_totals": services.sales_totals(pools),
        "kpis": kpis,
        "kpi_changes": kpi_changes,
        "chart": services.two_series_bar_chart(chart_series),
    }
    return render(request, "reports/dashboard.html", context)


@staff_member_required
@can_view_reports
def event_report(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    is_demo = _is_demo(request)

    revenue = services.event_revenue_summary(event, is_demo=is_demo)
    ticket_counts = services.event_ticket_counts(event, is_demo=is_demo)
    pools = services.pool_sales_report(is_demo=is_demo, event=event)
    curve = services.event_sales_curve(event, is_demo=is_demo)

    valid_tickets = ticket_counts["issued"] + ticket_counts["checked_in"]
    attendance_pct = (
        round(100 * ticket_counts["checked_in"] / valid_tickets, 1) if valid_tickets else None
    )

    pool_ticket_total = sum(p["issued"] + p["checked_in"] + p["refunded"] for p in pools) or 1
    for pool_row in pools:
        pool_row["share_pct"] = round(
            100 * (pool_row["issued"] + pool_row["checked_in"] + pool_row["refunded"])
            / pool_ticket_total,
            1,
        )

    context = {
        "event": event,
        "is_demo": is_demo,
        "currency": "DEMO" if is_demo else "PLN",
        "revenue": revenue,
        "ticket_counts": ticket_counts,
        "attendance_pct": attendance_pct,
        "pools": pools,
        "curve": curve,
        "curve_chart": services.single_series_bar_chart([row["revenue"] for row in curve]),
    }
    return render(request, "reports/event_report.html", context)


@staff_member_required
@can_view_reports
def sales_csv(request):
    is_demo = _is_demo(request)
    date_from, date_to = _date_range(request)
    filename = _export_filename("sprzedaz", request, date_from, date_to, is_demo, "csv")
    return _csv_response(
        filename, SALES_HEADER, _sales_rows(is_demo=is_demo, date_from=date_from, date_to=date_to)
    )


@staff_member_required
@can_view_reports
def sales_xlsx(request):
    is_demo = _is_demo(request)
    date_from, date_to = _date_range(request)
    filename = _export_filename("sprzedaz", request, date_from, date_to, is_demo, "xlsx")
    return _xlsx_response(
        filename, SALES_HEADER, _sales_rows(is_demo=is_demo, date_from=date_from, date_to=date_to)
    )


@staff_member_required
@can_view_reports
def pool_sales_csv(request):
    is_demo = _is_demo(request)
    date_from, date_to = _date_range(request)
    filename = _export_filename("pule", request, date_from, date_to, is_demo, "csv")
    return _csv_response(
        filename, POOL_HEADER, _pool_rows(is_demo=is_demo, date_from=date_from, date_to=date_to)
    )


@staff_member_required
@can_view_reports
def pool_sales_xlsx(request):
    is_demo = _is_demo(request)
    date_from, date_to = _date_range(request)
    filename = _export_filename("pule", request, date_from, date_to, is_demo, "xlsx")
    return _xlsx_response(
        filename, POOL_HEADER, _pool_rows(is_demo=is_demo, date_from=date_from, date_to=date_to)
    )


@staff_member_required
@can_view_reports
def cash_flow_csv(request):
    is_demo = _is_demo(request)
    date_from, date_to = _date_range(request)
    filename = _export_filename("przeplywy", request, date_from, date_to, is_demo, "csv")
    return _csv_response(
        filename, FLOW_HEADER, _flow_rows(is_demo=is_demo, date_from=date_from, date_to=date_to)
    )


@staff_member_required
@can_view_reports
def cash_flow_xlsx(request):
    is_demo = _is_demo(request)
    date_from, date_to = _date_range(request)
    filename = _export_filename("przeplywy", request, date_from, date_to, is_demo, "xlsx")
    return _xlsx_response(
        filename, FLOW_HEADER, _flow_rows(is_demo=is_demo, date_from=date_from, date_to=date_to)
    )


@staff_member_required
@can_view_reports
def global_search(request):
    """OBS-04: one search box across events, orders and tickets, reachable
    from the staff nav on any screen (kod biletu, e-mail, ID zamówienia,
    nazwa wydarzenia)."""
    from apps.orders.models import Order
    from apps.tickets.models import Ticket

    query = request.GET.get("q", "").strip()
    events = orders = tickets = []
    searched = len(query) >= 2
    if searched:
        events = list(Event.objects.filter(title__icontains=query).order_by("-start_at")[:10])
        orders = list(
            Order.objects.filter(Q(buyer_email__icontains=query) | Q(id__istartswith=query))
            .select_related("event", "pool")
            .order_by("-created_at")[:10]
        )
        tickets = list(
            Ticket.objects.filter(Q(short_code__icontains=query) | Q(buyer_email__icontains=query))
            .select_related("event", "order")[:10]
        )

    context = {
        "query": query,
        "searched": searched,
        "events": events,
        "orders": orders,
        "tickets": tickets,
        "has_results": bool(events or orders or tickets),
    }
    return render(request, "reports/search.html", context)
