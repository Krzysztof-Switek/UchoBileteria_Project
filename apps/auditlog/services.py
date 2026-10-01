"""Single entry point for writing audit log records."""

from .models import AuditLog


def event_of(entity):
    """The Event an audited object belongs to: itself, its `.event`, or None."""
    from apps.events.models import Event

    if isinstance(entity, Event):
        return entity
    event = getattr(entity, "event", None)
    return event if isinstance(event, Event) else None


def log_action(action: str, entity, *, actor=None, metadata: dict | None = None) -> AuditLog:
    """
    Write an audit record.

    `entity` is any model instance; its class name and pk are stored.
    `actor` is a User or None (system action).
    """
    role = ""
    if actor is not None:
        groups = list(actor.groups.values_list("name", flat=True))
        role = groups[0] if groups else ("ADMIN" if actor.is_superuser else "")
    return AuditLog.objects.create(
        actor=actor if (actor is not None and actor.pk) else None,
        actor_label=getattr(actor, "username", "") or "system",
        actor_role=role,
        action=action,
        entity_type=type(entity).__name__,
        entity_id=str(entity.pk),
        event=event_of(entity),
        metadata=metadata or {},
    )
