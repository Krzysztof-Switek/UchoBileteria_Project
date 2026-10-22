"""
Financial report of one event ("Raport sprzedaży" tile, 05.10.2026): tickets sold
per pool, gross / net / VAT, refunds, unpaid reservations, price zones. Can be
generated at any moment (a month before, on the day, after) — every figure is
"as of now" and the PDF carries the generation time.

Money basis: an order's amount kept by the club = amount_gross - refunded_amount
(partial refunds keep the order PAID). VAT is computed from gross at the rate in
settings.TICKET_VAT_RATE — the rate must be confirmed by the club's accountant.
"""

from decimal import ROUND_HALF_UP, Decimal
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.db.models import Count, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.events.models import Event, EventStatus
from apps.orders.models import Order, OrderStatus
from apps.tickets.models import Ticket, TicketStatus

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
VALID = [TicketStatus.ISSUED, TicketStatus.CHECKED_IN]


def vat_rate() -> Decimal:
    return Decimal(str(getattr(settings, "TICKET_VAT_RATE", 8)))


def split_gross(gross: Decimal) -> tuple[Decimal, Decimal]:
    """(net, vat) from a gross amount at the configured rate."""
    rate = vat_rate()
    net = (gross * 100 / (100 + rate)).quantize(CENT, rounding=ROUND_HALF_UP)
    return net, gross - net


def default_event(events) -> Event | None:
    """The event closest to now (upcoming or just past)."""
    now = timezone.now()
    events = list(events)
    if not events:
        return None
    return min(events, key=lambda e: abs((e.start_at - now).total_seconds()))


def report_events():
    return Event.objects.exclude(status=EventStatus.DRAFT).order_by("-start_at")


def _money(qs, field):
    return qs.aggregate(v=Coalesce(Sum(field), ZERO))["v"]


def event_finance(event: Event, *, is_demo: bool) -> dict:
    orders = Order.objects.filter(event=event, is_demo=is_demo)
    settled = orders.filter(status__in=[OrderStatus.PAID, OrderStatus.REFUNDED])
    tickets = Ticket.objects.filter(event=event, is_demo=is_demo)

    pools = []
    for pool in event.pools.order_by("sales_start_at", "pk"):
        po = settled.filter(pool=pool)
        received = _money(po, "amount_gross")
        refunded = _money(po, "refunded_amount")
        kept = received - refunded
        net, vat = split_gross(kept)
        pt = tickets.filter(pool=pool)
        sold = pt.filter(status__in=VALID).count()
        price_net, price_vat = split_gross(pool.price_gross)
        pools.append({
            "name": pool.name or "pula bez nazwy",
            "price": pool.price_gross,
            "price_net": price_net,
            "price_vat": price_vat,
            "capacity": pool.capacity,
            "sold": sold,
            "checked_in": pt.filter(status=TicketStatus.CHECKED_IN).count(),
            "refunded_tickets": pt.filter(status=TicketStatus.REFUNDED).count(),
            "received": received,
            "refunds": refunded,
            "gross": kept,
            "net": net,
            "vat": vat,
        })

    received = sum((p["received"] for p in pools), ZERO)
    refunds = sum((p["refunds"] for p in pools), ZERO)
    gross = sum((p["gross"] for p in pools), ZERO)
    net = sum((p["net"] for p in pools), ZERO)
    sold = sum(p["sold"] for p in pools)

    zones = []
    seat_map = getattr(event, "seat_map", None) if event.is_seated else None
    defined = (seat_map.layout_snapshot.get("zones", []) or []) if seat_map else []
    if defined:
        names = {z["code"]: z.get("name", "") for z in defined}
        rows = (tickets.filter(status__in=VALID, seat__isnull=False)
                .values("seat__category", "seat__price_percent")
                # tickets issued before 05.10 have no own price — fall back to the pool price
                .annotate(n=Count("pk"), v=Coalesce(Sum(Coalesce("price_gross",
                                                                 "pool__price_gross")), ZERO))
                .order_by("-seat__price_percent", "seat__category"))
        for row in rows:
            code = row["seat__category"]
            z_net, z_vat = split_gross(row["v"])
            zones.append({"code": code if code in names else "—",
                          "name": names.get(code, "bez strefy"),
                          "percent": row["seat__price_percent"], "sold": row["n"],
                          "net": z_net, "vat": z_vat, "gross": row["v"]})

    return {
        "event": event,
        "is_demo": is_demo,
        "currency": "DEMO" if is_demo else "PLN",
        "vat_rate": vat_rate(),
        "generated_at": timezone.localtime(),
        "pools": pools,
        "zones": zones,
        "totals": {
            "capacity": event.capacity_total,
            "sold": sold,
            "sold_pct": (round(100 * sold / event.capacity_total, 1)
                         if event.capacity_total else None),
            "checked_in": sum(p["checked_in"] for p in pools),
            "refunded_tickets": sum(p["refunded_tickets"] for p in pools),
            "received": received,
            "refunds": refunds,
            "gross": gross,
            "net": net,
            "vat": gross - net,
        },
        "sales_open": event.status == EventStatus.PUBLISHED and timezone.now() < event.sales_end_at,
    }


# --- PDF -----------------------------------------------------------------------------------

FONT_CANDIDATES = [
    # Linux (Docker: apt fonts-dejavu-core), then Windows (local dev)
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
]


def _register_font() -> tuple[str, str, bool]:
    """(regular, bold, unicode?) — a TTF with Polish letters if the system has
    one, else the built-in Helvetica with letters folded to ASCII."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for regular, bold in FONT_CANDIDATES:
        if Path(regular).exists() and Path(bold).exists():
            if "UchoSans" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("UchoSans", regular))
                pdfmetrics.registerFont(TTFont("UchoSans-Bold", bold))
            return "UchoSans", "UchoSans-Bold", True
    return "Helvetica", "Helvetica-Bold", False


def _fmt_money(value) -> str:
    if value is None:
        return "—"
    s = f"{Decimal(value):,.2f}"
    return s.replace(",", " ").replace(".", ",")


def finance_pdf(data: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

    font, bold, unicode_ok = _register_font()

    def t(text) -> str:
        text = str(text)
        if not unicode_ok:
            text = text.translate(
                str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ·–—„”", "acelnoszzACELNOSZZ---\"\""))
        return text

    event = data["event"]
    cur = data["currency"]
    tot = data["totals"]
    generated = data["generated_at"].strftime("%d.%m.%Y %H:%M")
    styles = {
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=12, leading=16, spaceAfter=2,
                             textColor=colors.HexColor("#555555")),
        "event": ParagraphStyle("event", fontName=bold, fontSize=18, leading=23, spaceAfter=3),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=11.5, leading=15, spaceBefore=10,
                             spaceAfter=4),
        "p": ParagraphStyle("p", fontName=font, fontSize=9, leading=12),
        "small": ParagraphStyle("small", fontName=font, fontSize=7.5, leading=10,
                                textColor=colors.HexColor("#555555")),
    }
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title=t(f"Raport finansowy — {event.title}"))

    def table(rows, widths, money_cols=(), total_row=False):
        tbl = Table([[t(c) for c in r] for r in rows], colWidths=widths, repeatRows=1)
        style = [
            ("FONT", (0, 0), (-1, -1), font, 8.5),
            ("FONT", (0, 0), (-1, 0), bold, 8.5),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8edf7")),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#8a94a6")),
            ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#d5d9e0")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        for col in money_cols:
            style.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
        if total_row:
            style += [("FONT", (0, -1), (-1, -1), bold, 8.5),
                      ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black)]
        tbl.setStyle(TableStyle(style))
        return tbl

    # Header (user's spec 05.10.2026): report name, event name + event date,
    # generation time; the DEMO line only in demo mode.
    story = [
        Paragraph(t("Raport finansowy"), styles["h1"]),
        Paragraph(t(f"{event.title} · {timezone.localtime(event.start_at):%d.%m.%Y}"),
                  styles["event"]),
        Paragraph(t(f"Wygenerowano: {generated}"), styles["small"]),
    ]
    if data["is_demo"]:
        story.append(Paragraph(t("TRYB DEMO — kwoty w walucie wirtualnej, nie są prawdziwymi "
                                 "wpływami."), styles["p"]))

    story += [Paragraph(t("Podsumowanie"), styles["h2"]), table([
        ["Pozycja", "Wartość"],
        ["Sprzedane bilety (ważne)", f"{tot['sold']} z {tot['capacity']}" + (
            f" ({str(tot['sold_pct']).replace('.', ',')}%)" if tot["sold_pct"] is not None
            else "")],
        ["Zwrócone bilety", str(tot["refunded_tickets"])],
        [f"Wpływy brutto ({cur})", _fmt_money(tot["received"])],
        [f"Zwroty brutto ({cur})", _fmt_money(tot["refunds"])],
        [f"Przychód netto ({cur})", _fmt_money(tot["net"])],
        [f"VAT {data['vat_rate']}% ({cur})", _fmt_money(tot["vat"])],
        [f"Przychód brutto ({cur})", _fmt_money(tot["gross"])],
    ], [120 * mm, 58 * mm], money_cols=(1,))]

    rows = [["Pula", "Cena netto", "Cena brutto", "Pula", "Sprzed.", "Zwróc.", "Netto", "VAT",
             "Brutto"]]
    for p in data["pools"]:
        rows.append([p["name"], _fmt_money(p["price_net"]), _fmt_money(p["price"]),
                     str(p["capacity"]), str(p["sold"]), str(p["refunded_tickets"]),
                     _fmt_money(p["net"]), _fmt_money(p["vat"]), _fmt_money(p["gross"])])
    rows.append(["Razem", "", "", str(sum(p["capacity"] for p in data["pools"])),
                 str(tot["sold"]), str(tot["refunded_tickets"]), _fmt_money(tot["net"]),
                 _fmt_money(tot["vat"]), _fmt_money(tot["gross"])])
    story += [Paragraph(t(f"Sprzedaż wg pul ({cur})"), styles["h2"]),
              table(rows, [32 * mm, 19 * mm, 19 * mm, 12 * mm, 13 * mm, 13 * mm, 23 * mm,
                           20 * mm, 23 * mm], money_cols=range(1, 9), total_row=True)]

    if data["zones"]:
        zrows = [["Strefa", "Nazwa", "% ceny", "Sprzedane", "Netto", "VAT", "Brutto"]]
        for z in data["zones"]:
            zrows.append([z["code"], z["name"], f"{z['percent']}%", str(z["sold"]),
                          _fmt_money(z["net"]), _fmt_money(z["vat"]), _fmt_money(z["gross"])])
        story += [Paragraph(t(f"Strefy cenowe — miejsca numerowane ({cur})"), styles["h2"]),
                  table(zrows, [16 * mm, 52 * mm, 18 * mm, 20 * mm, 25 * mm, 22 * mm, 25 * mm],
                        money_cols=(2, 3, 4, 5, 6))]

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont(font, 7.5)
        canvas.drawString(16 * mm, 9 * mm, t(f"{event.title} · raport wygenerowany {generated}"))
        canvas.drawRightString(A4[0] - 16 * mm, 9 * mm, t(f"strona {canvas.getPageNumber()}"))
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
