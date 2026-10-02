import uuid

from django.conf import settings
from django.db import models


class Lead(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новая"
        QUALIFYING = "qualifying", "На разборе"
        PROPOSAL = "proposal", "Готовится предложение"
        WON = "won", "Переведена в заказ"
        LOST = "lost", "Закрыта"

    class Source(models.TextChoices):
        FORM = "form", "Форма сайта"
        MESSENGER = "messenger", "Мессенджер"
        ADMIN = "admin", "Админка"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="leads"
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=160, blank=True)
    email = models.EmailField(blank=True)
    subject = models.CharField(max_length=220, blank=True)
    service_type = models.CharField(max_length=120, blank=True)
    task = models.TextField()
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.FORM)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    page_title = models.CharField(max_length=220, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = (
            models.Index(fields=("status", "created_at"), name="lead_status_created_idx"),
            models.Index(fields=("source", "created_at"), name="lead_source_created_idx"),
        )
        verbose_name = "Заявка"
        verbose_name_plural = "Заявки"

    def __str__(self):
        label = self.subject or self.service_type or self.email or self.name or "Заявка"
        return f"{label} #{self.pk}"


class QuickConsultation(models.Model):
    class PendingAction(models.TextChoices):
        MENU = "menu", "Меню"
        CONSULTATION = "consultation", "Консультация"
        PAYMENT = "payment", "Оплата"

    class Status(models.TextChoices):
        WAITING_OFFER = "waiting_offer", "Ждем принятия оферты"
        WAITING_PRIVACY = "waiting_privacy", "Ждем согласие с политикой"
        WAITING_EMAIL = "waiting_email", "Ждем email"
        WAITING_EMAIL_CONFIRM = "waiting_email_confirm", "Подтверждение email"
        WAITING_QUESTION = "waiting_question", "Ждем вопрос"
        WAITING_PAYMENT_AMOUNT = "waiting_payment_amount", "Ждем сумму оплаты"
        NEW = "new", "Новая"
        IN_DISCUSSION = "in_discussion", "В консультации"
        INVOICED = "invoiced", "Счет отправлен"
        PAID = "paid", "Оплачена"
        CLOSED = "closed", "Закрыта"
        CANCELLED = "cancelled", "Отменена"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="quick_consultations"
    )
    telegram_chat_id = models.CharField(max_length=80)
    telegram_user_id = models.CharField(max_length=80, blank=True)
    telegram_username = models.CharField(max_length=120, blank=True)
    first_name = models.CharField(max_length=120, blank=True)
    last_name = models.CharField(max_length=120, blank=True)
    client_name = models.CharField(max_length=180, blank=True)
    client_email = models.EmailField(blank=True)
    question = models.TextField(blank=True)
    manager_response = models.TextField(blank=True)
    quoted_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    invoice = models.OneToOneField(
        "billing.Invoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quick_consultation",
    )
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.WAITING_QUESTION)
    offer_accepted_at = models.DateTimeField(null=True, blank=True)
    privacy_accepted_at = models.DateTimeField(null=True, blank=True)
    pending_action = models.CharField(max_length=30, choices=PendingAction.choices, blank=True, default="")
    internal_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at", "-created_at")
        constraints = (
            models.CheckConstraint(
                condition=models.Q(quoted_amount__isnull=True) | models.Q(quoted_amount__gt=0),
                name="quick_quote_positive",
            ),
        )
        indexes = (
            models.Index(fields=("status", "updated_at"), name="quick_status_updated_idx"),
            models.Index(fields=("telegram_chat_id", "status", "updated_at"), name="quick_chat_state_idx"),
        )
        verbose_name = "Быстрая консультация"
        verbose_name_plural = "Быстрые консультации"

    def __str__(self):
        return f"Быстрая консультация #{self.pk}: {self.display_name}"

    @property
    def display_name(self):
        name = self.client_name or " ".join(part for part in (self.first_name, self.last_name) if part).strip()
        if name:
            return name
        if self.telegram_username:
            return self.telegram_username
        return f"Telegram chat {self.telegram_chat_id}"

    @property
    def telegram_label(self):
        parts = [self.display_name]
        if self.telegram_username:
            parts.append(self.telegram_username)
        parts.append(f"chat_id {self.telegram_chat_id}")
        return " · ".join(part for part in parts if part)
