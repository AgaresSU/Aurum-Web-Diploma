import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class Conversation(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Открыт"
        WAITING_CLIENT = "waiting_client", "Ждем клиента"
        WAITING_MANAGER = "waiting_manager", "Ждет ответа"
        CLOSED = "closed", "Закрыт"

    public_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    public_access_expires_at = models.DateTimeField(null=True, blank=True)
    public_access_revoked_at = models.DateTimeField(null=True, blank=True)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="conversations"
    )
    lead = models.ForeignKey(
        "leads.Lead", on_delete=models.SET_NULL, null=True, blank=True, related_name="conversations"
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    client_email = models.EmailField(blank=True)
    title = models.CharField(max_length=220)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.WAITING_MANAGER)
    client_last_read_at = models.DateTimeField(null=True, blank=True)
    manager_last_read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)
        indexes = (models.Index(fields=("status", "updated_at"), name="conversation_state_date_idx"),)
        verbose_name = "Диалог"
        verbose_name_plural = "Диалоги"

    def __str__(self):
        return self.title

    @property
    def public_access_deadline(self):
        if self.public_access_expires_at:
            return self.public_access_expires_at
        if self.created_at:
            days = max(int(getattr(settings, "AURUMWEB_PUBLIC_CONVERSATION_LINK_DAYS", 30)), 1)
            return self.created_at + timedelta(days=days)
        return None

    @property
    def public_access_available(self):
        deadline = self.public_access_deadline
        return self.public_access_revoked_at is None and (deadline is None or deadline > timezone.now())

    @property
    def public_write_available(self):
        return self.public_access_available and self.status != self.Status.CLOSED

    @property
    def client_unread_count(self):
        queryset = self.messages.exclude(author_role="client").filter(is_internal=False)
        if self.client_last_read_at:
            queryset = queryset.filter(created_at__gt=self.client_last_read_at)
        return queryset.count()

    @property
    def manager_unread_count(self):
        queryset = self.messages.filter(author_role="client", is_internal=False)
        if self.manager_last_read_at:
            queryset = queryset.filter(created_at__gt=self.manager_last_read_at)
        return queryset.count()


class Message(models.Model):
    class AuthorRole(models.TextChoices):
        CLIENT = "client", "Клиент"
        MANAGER = "manager", "Менеджер"
        SYSTEM = "system", "Система"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    author_role = models.CharField(max_length=20, choices=AuthorRole.choices)
    body = models.TextField()
    is_internal = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)
        verbose_name = "Сообщение"
        verbose_name_plural = "Сообщения"

    def __str__(self):
        return f"{self.get_author_role_display()}: {self.body[:60]}"
