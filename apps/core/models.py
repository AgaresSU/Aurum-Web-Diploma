from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class ImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise TypeError("Записи журнала нельзя изменять.")

    def delete(self):
        raise TypeError("Записи журнала нельзя удалять.")


class AuditEvent(models.Model):
    class Action(models.TextChoices):
        CREATE = "create", "Создание"
        UPDATE = "update", "Изменение"
        DELETE = "delete", "Удаление"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    action = models.CharField(max_length=16, choices=Action.choices)
    object_type = models.CharField(max_length=120)
    object_id = models.CharField(max_length=80)
    object_label = models.CharField(max_length=220)
    channel = models.CharField(max_length=32, default="office")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    objects = ImmutableQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at",)
        indexes = (
            models.Index(fields=("object_type", "object_id", "created_at"), name="audit_object_created_idx"),
            models.Index(fields=("actor", "created_at"), name="audit_actor_created_idx"),
            models.Index(fields=("action", "created_at"), name="audit_action_created_idx"),
        )
        verbose_name = "Событие аудита"
        verbose_name_plural = "Журнал действий"

    def __str__(self):
        return f"{self.get_action_display()}: {self.object_label}"

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise ValidationError("Записи журнала нельзя изменять.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Записи журнала нельзя удалять.")


class ConsentAcceptance(models.Model):
    class DocumentType(models.TextChoices):
        OFFER = "offer", "Публичная оферта"
        PRIVACY = "privacy", "Политика обработки данных"

    class DocumentScope(models.TextChoices):
        GENERAL = "general", "Основной сайт"
        FAST = "fast", "Быстрые консультации"

    class Channel(models.TextChoices):
        WEB = "web", "Сайт"
        ACCOUNT = "account", "Личный кабинет"
        TELEGRAM = "telegram", "Telegram-бот"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consent_acceptances",
    )
    subject_type = models.CharField(max_length=120)
    subject_id = models.CharField(max_length=80)
    document_type = models.CharField(max_length=20, choices=DocumentType.choices)
    document_scope = models.CharField(max_length=20, choices=DocumentScope.choices)
    document_version = models.CharField(max_length=40)
    document_checksum = models.CharField(max_length=64)
    channel = models.CharField(max_length=20, choices=Channel.choices)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    accepted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-accepted_at",)
        constraints = (
            models.UniqueConstraint(
                fields=(
                    "subject_type",
                    "subject_id",
                    "document_type",
                    "document_scope",
                    "document_checksum",
                    "channel",
                ),
                name="unique_document_acceptance",
            ),
        )
        indexes = (
            models.Index(fields=("subject_type", "subject_id", "accepted_at"), name="consent_subject_date_idx"),
            models.Index(fields=("document_type", "accepted_at"), name="consent_document_date_idx"),
        )
        verbose_name = "Принятие документа"
        verbose_name_plural = "Принятые документы"

    def __str__(self):
        return f"{self.get_document_type_display()} · {self.subject_type} #{self.subject_id}"


class OperationalAlert(models.Model):
    class Severity(models.TextChoices):
        WARNING = "warning", "Предупреждение"
        ERROR = "error", "Критическая ошибка"

    code = models.CharField(max_length=180, unique=True)
    severity = models.CharField(max_length=16, choices=Severity.choices)
    title = models.CharField(max_length=220)
    message = models.TextField()
    details_hash = models.CharField(max_length=64)
    is_active = models.BooleanField(default=True)
    first_detected_at = models.DateTimeField(default=timezone.now)
    last_detected_at = models.DateTimeField(default=timezone.now)
    last_notified_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-is_active", "-last_detected_at")
        indexes = (models.Index(fields=("is_active", "severity", "last_detected_at"), name="ops_alert_state_date_idx"),)
        verbose_name = "Эксплуатационное предупреждение"
        verbose_name_plural = "Эксплуатационные предупреждения"

    def __str__(self):
        return self.title
