from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="użytkownik",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # username/"system" snapshot
    actor_label = models.CharField(verbose_name="kto", max_length=150, blank=True)
    actor_role = models.CharField(verbose_name="rola", max_length=50, blank=True)
    action = models.CharField(verbose_name="akcja", max_length=100)
    # The event this entry belongs to (the event itself, or the event of the
    # order/ticket/pool it's about) — set by services.log_action, so the log
    # can be browsed per event like orders and tickets. Null for entries not
    # tied to any event (e.g. the payment mode switch).
    event = models.ForeignKey(
        "events.Event",
        verbose_name="wydarzenie",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    entity_type = models.CharField(verbose_name="typ obiektu", max_length=50)
    entity_id = models.CharField(verbose_name="ID obiektu", max_length=64)
    metadata = models.JSONField(verbose_name="szczegóły", default=dict, blank=True)
    created_at = models.DateTimeField(verbose_name="kiedy", auto_now_add=True)

    class Meta:
        verbose_name = "wpis audytu"
        verbose_name_plural = "wpisy audytu"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["action"]),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_label or 'system'}: {self.action}"
