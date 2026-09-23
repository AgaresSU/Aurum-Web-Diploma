import uuid

from django.conf import settings
from django.db import models


class ClientRequest(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новая задача"
        IN_PROGRESS = "in_progress", "В работе"
        COMPLETED = "completed", "Завершена"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="client_requests",
    )
    title = models.CharField(max_length=220)
    service_type = models.CharField(max_length=120)
    summary = models.TextField()
    template_slug = models.SlugField(max_length=160, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    due_at = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Задача клиента"
        verbose_name_plural = "Задачи клиентов"

    def __str__(self):
        return self.title

    @property
    def next_action(self):
        if self.status == self.Status.COMPLETED:
            return "Работа завершена"
        if self.status == self.Status.IN_PROGRESS:
            return "Следите за ходом работы в кабинете"
        return "Задача передана менеджеру"

    @property
    def manager(self):
        return None
