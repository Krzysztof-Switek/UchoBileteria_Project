from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.events.models import Event, EventStatus, TicketPool
from apps.seating import services
from apps.seating.layout import validate_layout
from apps.seating.models import Room, SeatingLayout


def _seats(obj_id: str, n: int) -> list[dict]:
    return [{"id": f"{obj_id}-s{i}", "label": str(i), "disabled": False} for i in range(1, n + 1)]


def cabaret_layout() -> dict:
    """12 tables on the main floor: round 6-seat tables in two columns left of
    the mezzanine columns, 4-seat ones on the narrow strip right of them.
    The first line (closest to the stage) sells only as whole tables."""
    objects = []
    number = 0
    for v in (640, 880, 1120, 1360):
        for u, r, n in ((300, 55, 6), (540, 55, 6), (870, 45, 4)):
            number += 1
            oid = f"t{number}"
            objects.append({
                "id": oid, "type": "table_round", "label": str(number), "x": u, "y": v,
                "r": r, "rotation": 0, "sell_mode": "TABLE" if v == 640 else "BOTH",
                "category": "przy scenie" if v == 640 else "", "seats": _seats(oid, n),
            })
    return {"schema_version": 1, "grid_cm": 100, "objects": objects}


def theatre_layout() -> dict:
    """Ten rows of 11 chairs between the side wall exits and the mezzanine columns."""
    objects = []
    for i, v in enumerate(range(600, 1411, 90)):
        letter = chr(ord("A") + i)
        oid = f"r{i + 1}"
        objects.append({
            "id": oid, "type": "row", "label": letter, "x": 440, "y": v, "spacing": 52,
            "rotation": 0, "sell_mode": "SEATS", "category": "", "numbering": "ltr",
            "start": 1, "seats": _seats(oid, 11),
        })
    return {"schema_version": 1, "grid_cm": 100, "objects": objects}


def max_layout() -> dict:
    """60 seats at 15 small (~1 m) round tables with price zones A/B/C: a staggered
    main block + one table per bay right of the mezzanine columns; aisles along
    the entrance wall and the column line, the bar front kept free."""
    tables = [
        (625, 222, "A"), (625, 472, "A"), (850, 347, "A"), (850, 597, "A"),
        (1075, 222, "B"), (1075, 472, "B"), (1300, 347, "B"), (1300, 597, "B"),
        (1525, 255, "C"), (1525, 505, "C"),
        (620, 895, "B"), (875, 895, "B"), (1130, 895, "C"), (1385, 895, "C"),
        (1640, 895, "C"),
    ]
    tables.sort(key=lambda t: (round(t[0] / 80), t[1]))
    objects = []
    for n, (v, u, zone) in enumerate(tables, start=1):
        oid = f"t{n}"
        objects.append({"id": oid, "type": "table_round", "label": str(n), "x": u, "y": v,
                        "r": 50, "rotation": 45, "sell_mode": "BOTH", "category": zone,
                        "seats": _seats(oid, 4)})
    zones = [{"code": "A", "name": "przy scenie", "percent": 120},
             {"code": "B", "name": "standard", "percent": 100},
             {"code": "C", "name": "pod antresolą / z tyłu", "percent": 80}]
    return {"schema_version": 1, "grid_cm": 100, "zones": zones, "objects": objects}


AUDIENCE_LAYOUT = "Widownia — 18 stolików, 84 miejsca przodem do sceny"


def audience_layout(room_geometry: dict) -> dict:
    """84 seats: chairs only on the audience side of ~1 m round tables, so everybody
    sees the stage over their own table. Main block: 12 tables x 5 chairs in staggered
    rows (each table looks between the two in front), aimed at the stage; strip right of
    the mezzanine columns: 6 tables x 4 chairs facing straight at the stage.
    Kept free: ~1 m along the entrance wall, the column line, the bar front.
    Zones: A (best, green) front rows, B middle + front of the strip, C the rest."""
    from apps.seating.layout import rotation_towards_stage

    main = [(220, 600, "A"), (470, 600, "A"), (345, 790, "A"), (595, 790, "A"),
            (220, 980, "B"), (470, 980, "B"), (345, 1170, "B"), (595, 1170, "B"),
            (220, 1360, "C"), (470, 1360, "C"), (345, 1550, "C"), (595, 1550, "C")]
    strip = [(880, 560, "B"), (880, 760, "B"), (880, 990, "C"), (880, 1215, "C"),
             (880, 1440, "C"), (880, 1670, "C")]
    tables = [(u, v, z, 5, rotation_towards_stage(u, v, room_geometry)) for u, v, z in main]
    tables += [(u, v, z, 4, 0) for u, v, z in strip]
    tables.sort(key=lambda t: (round(t[1] / 100), t[0]))  # from the stage, then left to right
    objects = []
    for n, (u, v, zone, seats, rotation) in enumerate(tables, start=1):
        oid = f"t{n}"
        objects.append({"id": oid, "type": "table_round", "label": str(n), "x": u, "y": v,
                        "r": 50, "rotation": rotation, "arrangement": "facing",
                        "sell_mode": "BOTH", "category": zone, "seats": _seats(oid, seats)})
    zones = [{"code": "A", "name": "najlepszy widok", "percent": 120},
             {"code": "B", "name": "standard", "percent": 100},
             {"code": "C", "name": "tańsze", "percent": 80}]
    return {"schema_version": 1, "grid_cm": 100, "zones": zones, "objects": objects}


class Command(BaseCommand):
    help = (
        "Demo of the seat planner: imports the hall (if missing), creates two example "
        "layouts and a published seated event with the cabaret layout attached."
    )

    def handle(self, *args, **options):
        if not Room.objects.exists():
            call_command("import_hall")
        room = Room.objects.order_by("pk").first()

        layouts = {}
        for name, builder, description in (
            ("Kabaret — 12 stolików", cabaret_layout,
             "Pierwsza linia przy scenie tylko jako całe stoliki; pozostałe miejsca albo stoliki."),
            ("Teatralny — 10 rzędów", theatre_layout, "Rzędy A–J po 11 krzeseł."),
            ("Maksymalny — 15 stolików Ø1 m (60 miejsc)", max_layout,
             "Stoliki ~1 m, 4 krzesła po przekątnych; przejścia wzdłuż ściany z wejściem i "
             "słupów antresoli, wolna strefa przy barze. Strefy cenowe A 120% / B 100% / C 80%."),
        ):
            layout, created = SeatingLayout.objects.get_or_create(
                room=room, name=name, defaults={"description": description}
            )
            if created or not layout.data.get("objects") or (
                "zones" in builder() and not layout.data.get("zones")
            ):
                services.save_layout(layout, builder())
            errors, warnings = validate_layout(layout.data, room.geometry)
            self.stdout.write(f"Układ „{layout.name}”: {layout.seat_count} miejsc, "
                              f"błędów {len(errors)}, ostrzeżeń {len(warnings)}.")
            for warning in warnings:
                self.stdout.write(f"  ⚠ {warning['message']}")
            layouts[name] = layout

        audience, created = SeatingLayout.objects.get_or_create(
            room=room, name=AUDIENCE_LAYOUT,
            defaults={"description": (
                "Stoliki ~1 m, krzesła tylko od strony widowni — każdy widzi scenę ponad swoim "
                "stołem. Blok główny 12 × 5 (rzędy przesunięte), pas za słupami 6 × 4. "
                "Przejścia: przy ścianie z wejściem, wzdłuż słupów antresoli, przed barem. "
                "Strefy: A 120% (zielona), B 100% (niebieska), C 80% (pomarańczowa)."
            )},
        )
        if created or not audience.data.get("objects"):
            services.save_layout(audience, audience_layout(room.geometry))
        errors, warnings = validate_layout(audience.data, room.geometry)
        self.stdout.write(f"Układ „{audience.name}”: {audience.seat_count} miejsc, "
                          f"błędów {len(errors)}, ostrzeżeń {len(warnings)}.")
        for warning in warnings:
            self.stdout.write(f"  ⚠ {warning['message']}")

        now = timezone.now()
        event, created = Event.objects.get_or_create(
            slug="kabaret-miejsca-numerowane",
            defaults={
                "title": "Wieczór kabaretowy (miejsca numerowane)",
                "description": "Wydarzenie demo z wyborem miejsc przy stolikach.",
                "gates_open_at": now + timedelta(days=14, hours=-1),
                "start_at": now + timedelta(days=14),
                "end_at": now + timedelta(days=14, hours=3),
                "sales_start_at": now - timedelta(days=1),
                "sales_end_at": now + timedelta(days=13),
                "capacity_total": 1,
                "max_tickets_per_order": 6,
                "status": EventStatus.DRAFT,
            },
        )
        if created:
            TicketPool.objects.create(event=event, name="Regular", price_gross=Decimal("89.00"),
                                      capacity=200)
        if not hasattr(event, "seat_map") and event.sold_total == 0:
            services.attach_layout(event, layouts["Kabaret — 12 stolików"])
            event.refresh_from_db()
        if event.status == EventStatus.DRAFT:
            event.status = EventStatus.PUBLISHED
            event.save(update_fields=["status"])
        self.stdout.write(self.style.SUCCESS(
            f"Wydarzenie: /wydarzenia/{event.slug}/ ({event.capacity_total} miejsc)."
        ))

        # second demo: price zones A/B/C on the maximal layout
        zoned, created = Event.objects.get_or_create(
            slug="stoliki-strefy-cenowe",
            defaults={
                "title": "Koncert przy stolikach — strefy cenowe",
                "description": "Demo: cena miejsca zależy od strefy (A 120%, B 100%, C 80%).",
                "gates_open_at": now + timedelta(days=21, hours=-1),
                "start_at": now + timedelta(days=21),
                "end_at": now + timedelta(days=21, hours=3),
                "sales_start_at": now - timedelta(days=1),
                "sales_end_at": now + timedelta(days=20),
                "capacity_total": 1,
                "max_tickets_per_order": 8,
                "status": EventStatus.DRAFT,
            },
        )
        if created:
            TicketPool.objects.create(event=zoned, name="Regular", price_gross=Decimal("90.00"),
                                      capacity=200)
        if not hasattr(zoned, "seat_map") and zoned.sold_total == 0:
            services.attach_layout(zoned, layouts["Maksymalny — 15 stolików Ø1 m (60 miejsc)"])
            zoned.refresh_from_db()
        if zoned.status == EventStatus.DRAFT:
            zoned.status = EventStatus.PUBLISHED
            zoned.save(update_fields=["status"])
        self.stdout.write(self.style.SUCCESS(
            f"Wydarzenie ze strefami: /wydarzenia/{zoned.slug}/ ({zoned.capacity_total} miejsc)."
        ))
