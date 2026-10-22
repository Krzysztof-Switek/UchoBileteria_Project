import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.seating.hall_import import HallFormatError, convert_hall
from apps.seating.models import Room
from apps.seating.room import apply_overrides, refresh_maps

DEFAULT_FILE = Path(settings.BASE_DIR) / "sala_glowna" / "sala_glowna.json"


class Command(BaseCommand):
    help = (
        "Import (or refresh) the hall geometry from the technical model export "
        "(sala_glowna/sala_glowna.json). Layouts and event maps are not touched."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", default=str(DEFAULT_FILE))
        parser.add_argument("--slug", default="sala-glowna")
        parser.add_argument(
            "--refresh-event-maps", action="store_true",
            help="Also redraw the hall on seat maps of upcoming events (seats and sales "
                 "are not touched — only the drawing of walls, stage, entrances…).",
        )

    def handle(self, *args, path, slug, refresh_event_maps=False, **options):
        file = Path(path)
        if not file.exists():
            raise CommandError(f"Brak pliku: {file}")
        try:
            data = convert_hall(json.loads(file.read_text(encoding="utf-8")))
        except (HallFormatError, KeyError, json.JSONDecodeError) as exc:
            raise CommandError(f"Nie udało się wczytać sali: {exc}") from exc

        room = Room.objects.filter(slug=slug).first()
        created = room is None
        if created:
            room = Room(slug=slug)
        for field in ("name", "width_cm", "depth_cm"):
            setattr(room, field, data[field])
        room.source = f"{file.name} ({data['geometry']['generated']})"
        room.base_geometry = data["geometry"]
        # the owner's adjustments (hidden / drawn elements) survive a re-import
        room.geometry = apply_overrides(room.base_geometry, room.overrides)
        room.save()
        if refresh_event_maps:
            updated = refresh_maps(room)
            self.stdout.write(f"Odświeżono rysunek sali na mapach {updated} wydarzeń.")
        verb = "Utworzono" if created else "Zaktualizowano"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} salę „{room.name}” ({room.width_cm}×{room.depth_cm} cm, "
            f"{len(room.geometry['obstacles'])} przeszkód)."
        ))
