from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    actor_label = models.CharField(max_length=150, blank=True)  # username/"system" snapshot
    actor_role = models.CharField(max_length=50, blank=True)
    action = models.CharField(max_length=100)
    entity_type = models.CharField(max_length=50)
    entity_id = models.CharField(max_length=64)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

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
