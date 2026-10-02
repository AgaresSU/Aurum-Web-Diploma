import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from .storage import private_project_storage
from .validators import project_file_upload_path, validate_project_file_extension, validate_project_file_size


class Project(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Подготовка"
        DISCOVERY = "discovery", "Уточнение задачи"
        WAITING_PAYMENT = "waiting_payment", "Ожидает оплату"
        IN_PROGRESS = "in_progress", "В работе"
        WAITING_CLIENT = "waiting_client", "Ожидает клиента"
        REVIEW = "review", "На согласовании"
        SUPPORT = "support", "На поддержке"
        COMPLETED = "completed", "Завершен"
        CANCELLED = "cancelled", "Отменен"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
    )
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_projects",
    )
    title = models.CharField(max_length=220)
    service_type = models.CharField(max_length=160, blank=True)
    client_name = models.CharField(max_length=160, blank=True)
    client_email = models.EmailField(blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DRAFT)
    summary = models.TextField(blank=True)
    next_action = models.CharField(max_length=240, blank=True)
    starts_at = models.DateField(null=True, blank=True)
    due_at = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at", "-created_at")
        indexes = (
            models.Index(fields=("status", "updated_at"), name="project_status_updated_idx"),
            models.Index(fields=("client_email", "updated_at"), name="project_email_updated_idx"),
            models.Index(fields=("due_at", "status"), name="project_due_status_idx"),
        )
        verbose_name = "Проект"
        verbose_name_plural = "Проекты"

    def __str__(self):
        return self.title

    @property
    def active_stage(self):
        return self.stages.filter(
            status__in=(ProjectStage.Status.IN_PROGRESS, ProjectStage.Status.WAITING_CLIENT)
        ).first()

    @property
    def days_left(self):
        if not self.due_at:
            return None
        return (self.due_at - timezone.localdate()).days


class ProjectStage(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Запланирован"
        IN_PROGRESS = "in_progress", "В работе"
        WAITING_CLIENT = "waiting_client", "Нужно согласование"
        COMPLETED = "completed", "Завершен"

    class ApprovalState(models.TextChoices):
        NOT_REQUIRED = "not_required", "Не требуется"
        PENDING = "pending", "Ожидает решения"
        APPROVED = "approved", "Согласовано"
        CHANGES_REQUESTED = "changes_requested", "Нужны изменения"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="stages")
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    result_summary = models.TextField(blank=True)
    result_url = models.URLField(blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    sort_order = models.PositiveSmallIntegerField(default=0)
    due_at = models.DateField(null=True, blank=True)
    approval_required = models.BooleanField(default=False)
    approval_state = models.CharField(
        max_length=24,
        choices=ApprovalState.choices,
        default=ApprovalState.NOT_REQUIRED,
    )
    approval_requested_at = models.DateTimeField(null=True, blank=True)
    approval_decided_at = models.DateTimeField(null=True, blank=True)
    approval_comment = models.TextField(blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_project_stages",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("sort_order", "created_at")
        indexes = (
            models.Index(fields=("project", "status", "sort_order"), name="stage_project_status_idx"),
            models.Index(fields=("project", "approval_state", "sort_order"), name="stage_project_approval_idx"),
        )
        verbose_name = "Этап проекта"
        verbose_name_plural = "Этапы проекта"

    def __str__(self):
        return f"{self.project}: {self.title}"


class ProjectFile(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="files")
    stage = models.ForeignKey(
        ProjectStage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="result_files",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="project_files",
    )
    title = models.CharField(max_length=220)
    file = models.FileField(
        storage=private_project_storage,
        upload_to=project_file_upload_path,
        validators=(validate_project_file_extension, validate_project_file_size),
    )
    original_name = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField(default=0)
    client_visible = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Файл проекта"
        verbose_name_plural = "Файлы проекта"

    def __str__(self):
        return self.title


class ProjectEvent(models.Model):
    class Kind(models.TextChoices):
        STATUS = "status", "Статус"
        MESSAGE = "message", "Сообщение"
        FILE = "file", "Файл"
        PAYMENT = "payment", "Оплата"
        APPROVAL = "approval", "Согласование"
        SITE = "site", "Сайт"
        NOTE = "note", "Заметка"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="events")
    project_file = models.ForeignKey(
        ProjectFile,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="project_events",
    )
    kind = models.CharField(max_length=24, choices=Kind.choices, default=Kind.STATUS)
    title = models.CharField(max_length=220)
    description = models.TextField(blank=True)
    client_visible = models.BooleanField(default=True)
    client_seen_at = models.DateTimeField(null=True, blank=True)
    manager_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = (
            models.Index(fields=("project", "client_visible", "created_at"), name="event_client_date_idx"),
            models.Index(fields=("manager_seen_at", "created_at"), name="event_manager_seen_idx"),
        )
        verbose_name = "Событие проекта"
        verbose_name_plural = "События проекта"

    def __str__(self):
        return self.title
