"""
Seat planner in the staff panel (Django admin, like the rest of the panel):

- /admin/seating/seatinglayout/                 — library of layouts (tiles)
- /admin/seating/seatinglayout/<id>/edytor/     — the layout editor
- /admin/seating/seatinglayout/<id>/dane/       — editor JSON (GET load, POST save)
- /admin/seating/seatinglayout/wydarzenie/<id>/ — seat map of one event (attach a
  layout, see who sits where, block seats, move a buyer to another seat)
- …/<id>/druk/, …/wydarzenie/<id>/druk/        — printable plan (browser "Zapisz jako PDF")
- …/<id>/wydarzenia/                            — events that used the layout
- …/sala/<id>/nowa-wersja/                      — a copy of the hall (room version), e.g.
  with the sound desk on the floor or a catwalk by the stage
"""

import json

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.text import slugify

from apps.events.models import Event

from . import services
from .layout import LayoutError, layout_summary
from .models import EventSeat, Room, SeatingLayout, SeatStatus

EMPTY_LAYOUT = {"schema_version": 1, "grid_cm": 100, "objects": []}


def _json_body(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


@admin.register(SeatingLayout)
class SeatingLayoutAdmin(admin.ModelAdmin):
    list_display = ["name", "room", "seat_count", "updated_at"]

    # -- permissions helpers -------------------------------------------------

    def _require(self, request, perm: str):
        if not request.user.has_perm(perm):
            raise PermissionDenied

    # -- URLs -------------------------------------------------------------------

    def get_urls(self):
        view = self.admin_site.admin_view
        return [
            path("nowy/", view(self.new_layout_view), name="seating_layout_new"),
            path("<int:layout_id>/edytor/", view(self.editor_view), name="seating_layout_editor"),
            path("<int:layout_id>/dane/", view(self.data_view), name="seating_layout_data"),
            path("<int:layout_id>/zapisz-jako/", view(self.save_as_view),
                 name="seating_layout_save_as"),
            path("<int:layout_id>/duplikuj/", view(self.duplicate_view),
                 name="seating_layout_duplicate"),
            path("<int:layout_id>/archiwizuj/", view(self.archive_view),
                 name="seating_layout_archive"),
            path("<int:layout_id>/sala/", view(self.layout_room_view),
                 name="seating_layout_room"),
            path("<int:layout_id>/wydarzenia/", view(self.layout_events_view),
                 name="seating_layout_events"),
            path("<int:layout_id>/druk/", view(self.layout_print_view),
                 name="seating_layout_print"),
            path("sala/<int:room_id>/", view(self.room_view), name="seating_room"),
            path("sala/<int:room_id>/nowa-wersja/", view(self.room_copy_view),
                 name="seating_room_copy"),
            path("wydarzenie/<int:event_id>/druk/", view(self.event_print_view),
                 name="seating_event_print"),
            path("wydarzenie/<int:event_id>/", view(self.event_map_view),
                 name="seating_event_map"),
            path("wydarzenie/<int:event_id>/stan.json", view(self.event_state_view),
                 name="seating_event_state"),
            path("wydarzenie/<int:event_id>/akcja/", view(self.event_action_view),
                 name="seating_event_action"),
        ] + super().get_urls()

    # -- library ---------------------------------------------------------------------

    def changelist_view(self, request, extra_context=None):
        self._require(request, "seating.view_seatinglayout")
        show_archived = request.GET.get("archiwum") == "1"
        layouts = (
            SeatingLayout.objects.filter(is_archived=show_archived)
            .select_related("room", "updated_by")
            .annotate(usage=Count("event_maps"), last_used=Max("event_maps__event__start_at"))
            .order_by("name")
        )
        layouts = list(layouts)
        rooms = {}
        for layout in layouts:
            rooms.setdefault(layout.room_id, self._room_payload(layout.room))
            layout.summary = layout_summary(layout.data)
        room_versions = Room.objects.annotate(
            layout_count=Count("layouts", distinct=True)).order_by("pk")
        context = {
            **self.admin_site.each_context(request),
            "title": "Plany sali",
            "layouts": layouts,
            "room_versions": room_versions,
            "show_room_column": len(room_versions) > 1,
            "thumbs": {lay.pk: {"room": lay.room_id, "layout": lay.data} for lay in layouts},
            "rooms": rooms,
            "show_archived": show_archived,
            "archived_count": SeatingLayout.objects.filter(is_archived=True).count(),
            "room": services.default_room(),
            "can_edit_room": request.user.has_perm("seating.change_room"),
            "can_add_room": request.user.has_perm("seating.add_room"),
            "can_add": request.user.has_perm("seating.add_seatinglayout"),
            "can_change": request.user.has_perm("seating.change_seatinglayout"),
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/seating/library.html", context)

    def layout_events_view(self, request, layout_id):
        """'użyty w N wydarzeniach' — which events used this layout."""
        self._require(request, "seating.view_seatinglayout")
        layout = get_object_or_404(SeatingLayout, pk=layout_id)
        now = timezone.now()
        maps = layout.event_maps.select_related("event").order_by("-event__start_at")
        rows = [{"event": m.event, "upcoming": m.event.start_at >= now} for m in maps]
        context = {
            **self.admin_site.each_context(request),
            "title": f"Wydarzenia z układem „{layout.name}”",
            "layout": layout,
            "rows": rows,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/seating/layout_events.html", context)

    def add_view(self, request, form_url="", extra_context=None):
        return redirect("admin:seating_seatinglayout_changelist")

    def change_view(self, request, object_id, form_url="", extra_context=None):
        return redirect("admin:seating_layout_editor", layout_id=object_id)

    def new_layout_view(self, request):
        self._require(request, "seating.add_seatinglayout")
        if request.method != "POST":
            return redirect("admin:seating_seatinglayout_changelist")
        room = services.default_room()
        if room is None:
            messages.error(request, "Najpierw wczytaj salę: manage.py import_hall.")
            return redirect("admin:seating_seatinglayout_changelist")
        name = (request.POST.get("name") or "").strip()[:200] or "Nowy układ"
        room = Room.objects.filter(pk=request.POST.get("room") or 0).first() or room
        layout = SeatingLayout.objects.create(
            room=room, name=name, data=EMPTY_LAYOUT,
            created_by=request.user, updated_by=request.user,
        )
        return redirect("admin:seating_layout_editor", layout_id=layout.pk)

    @staticmethod
    def _back_to_library(request):
        url = reverse("admin:seating_seatinglayout_changelist")
        return redirect(url + ("?archiwum=1" if request.POST.get("archiwum") == "1" else ""))

    def duplicate_view(self, request, layout_id):
        self._require(request, "seating.add_seatinglayout")
        if request.method != "POST":
            return redirect("admin:seating_seatinglayout_changelist")
        source = get_object_or_404(SeatingLayout, pk=layout_id)
        try:
            copy = services.copy_layout(source, f"{source.name} (kopia)", actor=request.user)
        except LayoutError as exc:
            messages.error(request, f"Nie można skopiować układu: {exc}")
            return self._back_to_library(request)
        messages.success(request, f"Utworzono kopię „{copy.name}”.")
        return redirect("admin:seating_layout_editor", layout_id=copy.pk)

    def archive_view(self, request, layout_id):
        self._require(request, "seating.change_seatinglayout")
        if request.method != "POST":
            return redirect("admin:seating_seatinglayout_changelist")
        layout = get_object_or_404(SeatingLayout, pk=layout_id)
        layout.is_archived = not layout.is_archived
        layout.updated_by = request.user
        layout.save(update_fields=["is_archived", "updated_by", "updated_at"])
        verb = "Zarchiwizowano" if layout.is_archived else "Przywrócono"
        messages.success(request, f"{verb} układ „{layout.name}”.")
        return self._back_to_library(request)

    # -- editor -------------------------------------------------------------------------

    def _room_payload(self, room: Room) -> dict:
        return {"name": room.name, "width_cm": room.width_cm, "depth_cm": room.depth_cm,
                **room.geometry}

    def editor_view(self, request, layout_id):
        self._require(request, "seating.view_seatinglayout")
        layout = get_object_or_404(SeatingLayout.objects.select_related("room"), pk=layout_id)
        usage = services.layout_usage(layout)
        can_change = request.user.has_perm("seating.change_seatinglayout")
        config = {
            "room": self._room_payload(layout.room),
            "layout": layout.data,
            "name": layout.name,
            "description": layout.description,
            # optimistic lock: a save from a tab opened before someone else's save is refused
            "version": layout.updated_at.isoformat(),
            "can_change": can_change,
            "can_add": request.user.has_perm("seating.add_seatinglayout"),
            "urls": {
                "room": reverse("admin:seating_layout_room", args=[layout.pk]),
                "data": reverse("admin:seating_layout_data", args=[layout.pk]),
                "save_as": reverse("admin:seating_layout_save_as", args=[layout.pk]),
                "library": reverse("admin:seating_seatinglayout_changelist"),
            },
        }
        context = {
            **self.admin_site.each_context(request),
            "title": layout.name,
            "layout": layout,
            "usage": usage,
            "room_versions": Room.objects.order_by("pk"),
            "editor_config": config,
            "can_change": can_change,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/seating/editor.html", context)

    def data_view(self, request, layout_id):
        layout = get_object_or_404(SeatingLayout.objects.select_related("room"), pk=layout_id)
        if request.method == "GET":
            self._require(request, "seating.view_seatinglayout")
            return JsonResponse({"layout": layout.data, "name": layout.name})
        self._require(request, "seating.change_seatinglayout")
        body = _json_body(request)
        if body.get("version") and body["version"] != layout.updated_at.isoformat():
            who = layout.updated_by or "ktoś inny"
            return JsonResponse({"ok": False, "stale": True, "errors": [{"message": (
                f"Ten układ zmienił w międzyczasie {who} "
                f"({layout.updated_at.astimezone():%d.%m %H:%M}). Odśwież stronę, żeby nie "
                "nadpisać tamtych zmian (Twoje możesz zapisać jako nowy układ)."
            )}]}, status=409)
        name = (body.get("name") or layout.name).strip()[:200] or layout.name
        description = body.get("description")
        # A layout used by events that are still ahead changes their seat maps
        # too (06.10.2026) — first time round the editor gets the list of those
        # events and asks for confirmation; nothing is saved until confirmed.
        if not body.get("confirm"):
            try:
                affected = services.propagation_preview(layout, body.get("data"))
            except LayoutError as exc:
                return JsonResponse({"ok": False, "errors": exc.problems}, status=400)
            if affected:
                return JsonResponse({"ok": False, "confirm": True, "events": affected})
        try:
            warnings = services.save_layout(
                layout, body.get("data"), request.user, name=name,
                description=description[:2000] if isinstance(description, str) else None,
            )
        except LayoutError as exc:
            return JsonResponse({"ok": False, "errors": exc.problems}, status=400)
        updated, refused = [], []
        for seat_map in services.upcoming_maps(layout):
            result = services.sync_event_map(seat_map.event, layout, actor=request.user)
            (updated if result["applied"] else refused).append(
                (seat_map.event, result["conflicts"]))
        message = "Zapisano."
        if updated:
            message += " Zaktualizowano plan sali na: " + ", ".join(
                f"„{e.title}” ({e.start_at.astimezone():%d.%m.%y})" for e, _ in updated) + "."
        for event, conflicts in refused:
            message += (f" NIE zmieniono planu na „{event.title}” — sprzedane lub zarezerwowane "
                        f"miejsca zniknęłyby z układu: {', '.join(conflicts[:8])}"
                        f"{'…' if len(conflicts) > 8 else ''}.")
        return JsonResponse({
            "ok": True, "warnings": warnings, "seat_count": layout.seat_count,
            "usage": len(services.layout_usage(layout)),
            "version": layout.updated_at.isoformat(),
            "updated_events": len(updated), "refused_events": len(refused),
            "message": message,
        })

    def layout_room_view(self, request, layout_id):
        """Switch the layout to another hall version (e.g. with the sound desk on
        the floor). Seats stay; upcoming events using the layout are redrawn —
        first call returns them for confirmation, like saving the layout."""
        self._require(request, "seating.change_seatinglayout")
        if request.method != "POST":
            return JsonResponse({"ok": False}, status=405)
        layout = get_object_or_404(SeatingLayout.objects.select_related("room"), pk=layout_id)
        body = _json_body(request)
        room = Room.objects.filter(pk=body.get("room") or 0).first()
        if room is None:
            return JsonResponse({"ok": False, "errors": [{"message": "Nie ma takiej sali."}]},
                                status=400)
        if not body.get("confirm"):
            affected = services.propagation_preview(layout, layout.data)
            if affected:
                return JsonResponse({"ok": False, "confirm": True, "events": affected})
        layout.room = room
        try:
            services.save_layout(layout, layout.data, request.user)
        except LayoutError as exc:
            return JsonResponse({"ok": False, "errors": exc.problems}, status=400)
        changed = sum(
            services.sync_event_map(m.event, layout, actor=request.user)["applied"]
            for m in services.upcoming_maps(layout)
        )
        messages.success(request, f"Układ korzysta teraz z sali „{room.name}”."
                         + (f" Zaktualizowano plan na {changed} wydarzeniach." if changed else ""))
        return JsonResponse({"ok": True})

    def save_as_view(self, request, layout_id):
        self._require(request, "seating.add_seatinglayout")
        if request.method != "POST":
            return JsonResponse({"ok": False}, status=405)
        source = get_object_or_404(SeatingLayout, pk=layout_id)
        body = _json_body(request)
        name = (body.get("name") or "").strip()[:200]
        if not name:
            return JsonResponse({"ok": False, "errors": [{"message": "Podaj nazwę układu."}]},
                                status=400)
        try:
            copy = services.copy_layout(source, name, actor=request.user, data=body.get("data"))
        except LayoutError as exc:
            return JsonResponse({"ok": False, "errors": exc.problems}, status=400)
        return JsonResponse({
            "ok": True, "id": copy.pk,
            "url": reverse("admin:seating_layout_editor", args=[copy.pk]),
        })

    # -- the hall itself ("Ustawienia sali") -------------------------------------------------

    def room_view(self, request, room_id):
        """Hide elements of the hall / draw own ones. Viewing: anyone with
        view_room; changing: change_room — ADMIN only (roles.ROOM_EDIT_PERMISSIONS)."""
        from apps.auditlog.services import log_action

        from .room import CUSTOM_KINDS, refresh_maps, save_overrides

        self._require(request, "seating.view_room")
        room = get_object_or_404(Room, pk=room_id)
        can_edit = request.user.has_perm("seating.change_room")
        if request.method == "POST":
            if not can_edit:
                raise PermissionDenied
            body = _json_body(request)
            try:
                if body.get("reset"):
                    room.overrides = {}
                    room.geometry = room.base_geometry
                    room.save(update_fields=["overrides", "geometry", "updated_at"])
                    log_action("seating.room_reset", room, actor=request.user)
                    refreshed = refresh_maps(room) if body.get("refresh") else 0
                else:
                    refreshed = save_overrides(room, body, actor=request.user,
                                               refresh_event_maps=bool(body.get("refresh")))
            except ValidationError as exc:
                return JsonResponse({"ok": False, "message": "; ".join(exc.messages)},
                                    status=400)
            message = "Zapisano ustawienia sali."
            if refreshed:
                message += f" Odświeżono rysunek na mapach {refreshed} wydarzeń."
            return JsonResponse({"ok": True, "message": message})

        context = {
            **self.admin_site.each_context(request),
            "title": f"Ustawienia sali — {room.name}",
            "room": room,
            "can_edit": can_edit,
            "kinds": CUSTOM_KINDS,
            "room_config": {
                "base": {"name": room.name, "width_cm": room.width_cm,
                         "depth_cm": room.depth_cm, **(room.base_geometry or room.geometry)},
                "overrides": room.overrides or {},
                "can_edit": can_edit,
                "urls": {"save": reverse("admin:seating_room", args=[room.pk])},
            },
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/seating/room.html", context)

    def room_copy_view(self, request, room_id):
        """'Nowa wersja sali': a copy of the hall with its own changes, e.g. the
        sound desk on the floor or a catwalk by the stage; layouts pick it."""
        from apps.auditlog.services import log_action

        self._require(request, "seating.add_room")
        if request.method != "POST":
            return redirect("admin:seating_seatinglayout_changelist")
        source = get_object_or_404(Room, pk=room_id)
        name = (request.POST.get("name") or "").strip()[:200] or f"{source.name} (wersja)"
        base_slug = slugify(name.replace("ł", "l").replace("Ł", "L"))[:40] or "sala"
        slug, n = base_slug, 2
        while Room.objects.filter(slug=slug).exists():
            slug, n = f"{base_slug}-{n}", n + 1
        room = Room.objects.create(
            name=name, slug=slug, width_cm=source.width_cm, depth_cm=source.depth_cm,
            geometry=source.geometry, base_geometry=source.base_geometry,
            overrides=source.overrides, source=source.source,
        )
        log_action("seating.room_copied", room, actor=request.user,
                   metadata={"source_id": source.pk, "source_name": source.name})
        messages.success(request, f"Utworzono wersję sali „{room.name}” — wprowadź zmiany i "
                         "zapisz, a potem wybierz ją w układzie.")
        return redirect("admin:seating_room", room_id=room.pk)

    # -- printing ----------------------------------------------------------------------------

    def layout_print_view(self, request, layout_id):
        self._require(request, "seating.view_seatinglayout")
        layout = get_object_or_404(SeatingLayout.objects.select_related("room"), pk=layout_id)
        summary = layout_summary(layout.data)
        return TemplateResponse(request, "admin/seating/print.html", {
            "heading": layout.name,
            "subheading": f"Sala: {layout.room.name}",
            "seat_count": layout.seat_count,
            "summary": summary,
            "description": layout.description,
            "config": {"room": self._room_payload(layout.room), "layout": layout.data,
                       "states": {}},
            "back_url": reverse("admin:seating_seatinglayout_changelist"),
            "generated_at": timezone.localtime(),
        })

    def event_print_view(self, request, event_id):
        event = self._event_or_404(request, event_id)
        seat_map = getattr(event, "seat_map", None)
        if seat_map is None:
            return redirect("admin:seating_event_map", event_id=event.pk)
        counts = {s: 0 for s in SeatStatus.values}
        states = {}
        for seat in EventSeat.objects.filter(event=event):
            states[seat.seat_key] = {"status": seat.status}
            counts[seat.status] += 1
        return TemplateResponse(request, "admin/seating/print.html", {
            "heading": event.title,
            "subheading": (f"{timezone.localtime(event.start_at):%d.%m.%Y %H:%M} · "
                           f"układ: {seat_map.source_layout_name}"),
            "seat_count": len(states),
            "summary": layout_summary(seat_map.layout_snapshot),
            "counts": counts,
            "config": {"room": seat_map.room_snapshot, "layout": seat_map.layout_snapshot,
                       "states": states},
            "back_url": reverse("admin:seating_event_map", args=[event.pk]),
            "generated_at": timezone.localtime(),
        })

    # -- event seat map ---------------------------------------------------------------------

    def _event_or_404(self, request, event_id) -> Event:
        if not request.user.has_perm("events.view_event"):
            raise PermissionDenied
        return get_object_or_404(Event, pk=event_id)

    def event_map_view(self, request, event_id):
        event = self._event_or_404(request, event_id)
        can_change = request.user.has_perm("events.change_event")
        if request.method == "POST":
            if not can_change:
                raise PermissionDenied
            return self._event_map_post(request, event)

        seat_map = getattr(event, "seat_map", None)
        layouts = SeatingLayout.objects.filter(is_archived=False).select_related("room")
        counts = {s: 0 for s in SeatStatus.values}
        for row in EventSeat.objects.filter(event=event).values("status").annotate(n=Count("id")):
            counts[row["status"]] = row["n"]
        rooms = {}
        for layout in layouts:
            rooms.setdefault(layout.room_id, self._room_payload(layout.room))
        context = {
            **self.admin_site.each_context(request),
            "title": f"Plan sali — {event.title}",
            "event": event,
            "seat_map": seat_map,
            "layouts": layouts,
            "thumbs": {lay.pk: {"room": lay.room_id, "layout": lay.data} for lay in layouts},
            "rooms": rooms,
            "locked_reason": services.structure_locked_reason(event),
            "counts": counts,
            "occupancy": services.occupancy_by_group(event) if seat_map else [],
            "pools_total": sum(p.capacity for p in event.pools.all()),
            "can_change": can_change,
            "map_config": {
                **(services.seat_map_payload(event) or {}),
                "urls": {
                    "state": reverse("admin:seating_event_state", args=[event.pk]),
                    "action": reverse("admin:seating_event_action", args=[event.pk]),
                },
                "can_change": can_change,
            } if seat_map else None,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/seating/event_map.html", context)

    def _event_map_post(self, request, event):
        action = request.POST.get("action")
        try:
            if action == "attach":
                layout = get_object_or_404(SeatingLayout, pk=request.POST.get("layout"))
                notes = services.attach_layout(event, layout, actor=request.user)
                messages.success(request, f"Przypięto układ „{layout.name}” — "
                                          f"{event.seats.count()} miejsc numerowanych.")
                for note in notes:
                    messages.warning(request, note)
            elif action == "detach":
                services.detach_layout(event, actor=request.user)
                messages.success(request, "Wydarzenie sprzedaje teraz bilety bez miejsc.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("admin:seating_event_map", event_id=event.pk)

    def event_state_view(self, request, event_id):
        event = self._event_or_404(request, event_id)
        from apps.tickets.models import Ticket

        tickets = {}
        for t in Ticket.objects.filter(event=event, seat__isnull=False).exclude(
            status__in=["REFUNDED", "CANCELLED", "INVALIDATED"]
        ).values("seat__seat_key", "short_code", "status", "id"):
            tickets[t["seat__seat_key"]] = {
                "code": t["short_code"], "checked_in": t["status"] == "CHECKED_IN",
                "id": str(t["id"]),
            }
        here = reverse("admin:seating_event_map", args=[event.pk])
        states = {}
        for seat in EventSeat.objects.filter(event=event).select_related("order"):
            info = {"status": seat.status, "label": seat.label}
            if seat.category:
                info["zone"] = f"{seat.category} ({seat.price_percent}%)"
            if seat.order_id:
                info.update({
                    "order": seat.order.short_id,
                    "order_url": reverse("admin:orders_order_change", args=[seat.order_id]),
                    "email": seat.order.buyer_email,
                    "order_status": seat.order.get_status_display(),
                })
            if seat.block_note:
                info["note"] = seat.block_note
            if seat.seat_key in tickets:
                ticket = tickets[seat.seat_key]
                info["ticket"] = ticket["code"]
                info["checked_in"] = ticket["checked_in"]
                if seat.status == SeatStatus.SOLD and not ticket["checked_in"]:
                    info["refund_url"] = (
                        reverse("admin:orders_order_partial_refund", args=[seat.order_id])
                        + f"?bilet={ticket['id']}&wroc={here}"
                    )
            states[seat.seat_key] = info
        event.refresh_from_db()
        return JsonResponse({
            "states": states,
            "capacity": event.capacity_total,
            "sold_total": event.sold_total,
        })

    def event_action_view(self, request, event_id):
        event = self._event_or_404(request, event_id)
        if request.method != "POST":
            return JsonResponse({"ok": False}, status=405)
        if not request.user.has_perm("events.change_event"):
            raise PermissionDenied
        body = _json_body(request)
        action = body.get("action")
        seats = [k for k in body.get("seats", []) if isinstance(k, str)][:500]
        try:
            if action == "block":
                n = services.block_seats(event, seats, body.get("note", ""), actor=request.user)
                return JsonResponse({"ok": True, "message": f"Zablokowano miejsc: {n}."})
            if action == "unblock":
                n = services.unblock_seats(event, seats, actor=request.user)
                return JsonResponse({"ok": True, "message": f"Odblokowano miejsc: {n}."})
            if action == "move":
                if not request.user.has_perm("orders.change_order"):
                    raise PermissionDenied
                order = services.move_seat(
                    event, str(body.get("from", "")), str(body.get("to", "")),
                    actor=request.user, notify=bool(body.get("notify", True)),
                )
                return JsonResponse({
                    "ok": True,
                    "message": f"Przeniesiono: zamówienie {order.short_id} "
                               f"({order.buyer_email}) — teraz {', '.join(order.seat_labels)}.",
                })
        except ValidationError as exc:
            return JsonResponse({"ok": False, "message": "; ".join(exc.messages)}, status=400)
        return JsonResponse({"ok": False, "message": "Nieznana akcja."}, status=400)


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    """Read-only: the hall comes from `manage.py import_hall`, not from hand edits."""

    list_display = ["name", "width_cm", "depth_cm", "source", "updated_at"]
    readonly_fields = ["name", "slug", "width_cm", "depth_cm", "source", "created_at",
                       "updated_at"]
    exclude = ["geometry"]

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

