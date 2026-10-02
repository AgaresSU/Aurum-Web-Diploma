from django.db import models
from django.utils import timezone

from apps.accounts.crypto import decrypt_value, encrypt_value
from apps.core.redaction import sanitize_log_data


class IntegrationEvent(models.Model):
    class Provider(models.TextChoices):
        ROBOKASSA = "robokassa", "Robokassa"
        TELEGRAM = "telegram", "Telegram"
        SYSTEM = "system", "Система"

    class Status(models.TextChoices):
        INFO = "info", "Информация"
        SUCCESS = "success", "Успешно"
        WARNING = "warning", "Предупреждение"
        ERROR = "error", "Ошибка"

    provider = models.CharField(max_length=30, choices=Provider.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INFO)
    title = models.CharField(max_length=220)
    payload = models.JSONField(default=dict, blank=True)
    payload_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = (
            models.Index(fields=("provider", "created_at"), name="integration_provider_date_idx"),
            models.Index(fields=("status", "created_at"), name="integration_status_date_idx"),
        )
        verbose_name = "Событие интеграции"
        verbose_name_plural = "События интеграций"

    def __str__(self):
        return f"{self.get_provider_display()}: {self.title}"

    def save(self, *args, **kwargs):
        if not self.payload_expires_at or self.payload_expires_at <= timezone.now():
            self.payload = sanitize_log_data(self.payload)
        return super().save(*args, **kwargs)


class OutboundTask(models.Model):
    class TaskType(models.TextChoices):
        TELEGRAM_UPDATE = "telegram_update", "Обработка Telegram webhook"
        QUICK_TELEGRAM_METHOD = "quick_telegram_method", "Отправка метода быстрого Telegram-бота"
        QUICK_WEBHOOK_EVENT = "quick_webhook_event", "Запись webhook быстрого Telegram-бота"
        QUICK_CONSULTATION_CREATED = "quick_consultation_created", "Новая быстрая консультация"
        INVOICE_ISSUED_EMAIL = "invoice_issued_email", "Email о выставленном счете"
        INVOICE_ISSUED_ADMIN_TELEGRAM = "invoice_issued_admin_tg", "Счет: Telegram администратору"
        INVOICE_ISSUED_CLIENT_TELEGRAM = "invoice_issued_client_tg", "Счет: Telegram клиенту"
        PAYMENT_SUCCEEDED_ADMIN_EMAIL = "payment_success_admin_email", "Оплата: email администратору"
        PAYMENT_SUCCEEDED_CLIENT_EMAIL = "payment_success_client_email", "Оплата: email клиенту"
        PAYMENT_SUCCEEDED_ADMIN_TELEGRAM = "payment_success_admin_tg", "Оплата: Telegram администратору"
        PAYMENT_SUCCEEDED_CLIENT_TELEGRAM = "payment_success_client_tg", "Оплата: Telegram клиенту"
        QUICK_PAYMENT_SUCCEEDED = "quick_payment_success", "Оплата: быстрый Telegram-бот"
        DUPLICATE_PAYMENT_EMAIL = "duplicate_payment_email", "Повторная оплата: email"
        DUPLICATE_PAYMENT_TELEGRAM = "duplicate_payment_tg", "Повторная оплата: Telegram"
        PAYMENT_REVIEW_EMAIL = "payment_review_email", "Спорная оплата: email"
        PAYMENT_REVIEW_TELEGRAM = "payment_review_tg", "Спорная оплата: Telegram"
        DEADLINE_REMINDER_EMAIL = "deadline_reminder_email", "Срок: email клиенту"
        DEADLINE_REMINDER_TELEGRAM = "deadline_reminder_tg", "Срок: Telegram клиенту"
        PROJECT_COMPLETED_TELEGRAM = "project_completed_tg", "Проект завершен: Telegram клиенту"

    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает"
        PROCESSING = "processing", "Выполняется"
        RETRY = "retry", "Ожидает повтора"
        SUCCEEDED = "succeeded", "Выполнено"
        FAILED = "failed", "Требует внимания"

    task_type = models.CharField(max_length=64, choices=TaskType.choices)
    payload = models.JSONField(default=dict, blank=True)
    deduplication_key = models.CharField(max_length=255, unique=True, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    locked_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at",)
        indexes = (
            models.Index(fields=("status", "available_at"), name="out_task_status_available"),
            models.Index(fields=("task_type", "created_at"), name="out_task_type_created"),
        )
        verbose_name = "Исходящая операция"
        verbose_name_plural = "Исходящие операции"

    def __str__(self):
        return f"{self.get_task_type_display()} #{self.pk}"


class TelegramBotSettings(models.Model):
    is_enabled = models.BooleanField(default=False)
    bot_token = models.TextField(blank=True)
    bot_username = models.CharField(max_length=80, blank=True)
    admin_chat_id = models.CharField(max_length=80, blank=True)
    webhook_secret = models.TextField(blank=True)
    webhook_url = models.URLField(blank=True)
    last_update_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Настройки Telegram-бота"
        verbose_name_plural = "Настройки Telegram-бота"

    def __str__(self):
        return "Telegram-бот AurumWeb"

    @classmethod
    def load(cls):
        config = cls.objects.order_by("pk").first()
        if config:
            return config
        return cls.objects.create()

    @property
    def has_bot_token(self):
        return bool(self.get_bot_token())

    @property
    def has_webhook_secret(self):
        return bool(self.get_webhook_secret())

    @property
    def configured(self):
        return bool(self.is_enabled and self.has_bot_token and self.admin_chat_id)

    def get_bot_token(self):
        return decrypt_value(self.bot_token)

    def set_bot_token(self, value):
        self.bot_token = encrypt_value(value.strip()) if value else ""

    def get_webhook_secret(self):
        return decrypt_value(self.webhook_secret)

    def set_webhook_secret(self, value):
        self.webhook_secret = encrypt_value(value.strip()) if value else ""


class QuickConsultationBotSettings(models.Model):
    is_enabled = models.BooleanField(default=False)
    bot_token = models.TextField(blank=True)
    bot_username = models.CharField(max_length=80, blank=True)
    admin_chat_id = models.CharField(max_length=80, blank=True)
    webhook_secret = models.TextField(blank=True)
    webhook_url = models.URLField(blank=True)
    last_update_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Настройки бота быстрых консультаций"
        verbose_name_plural = "Настройки бота быстрых консультаций"

    def __str__(self):
        return "Бот быстрых консультаций AurumWeb"

    @classmethod
    def load(cls):
        config = cls.objects.order_by("pk").first()
        if config:
            return config
        return cls.objects.create()

    @property
    def has_bot_token(self):
        return bool(self.get_bot_token())

    @property
    def has_webhook_secret(self):
        return bool(self.get_webhook_secret())

    @property
    def configured(self):
        return bool(self.is_enabled and self.has_bot_token and self.admin_chat_id)

    def get_bot_token(self):
        return decrypt_value(self.bot_token)

    def set_bot_token(self, value):
        self.bot_token = encrypt_value(value.strip()) if value else ""

    def get_webhook_secret(self):
        return decrypt_value(self.webhook_secret)

    def set_webhook_secret(self, value):
        self.webhook_secret = encrypt_value(value.strip()) if value else ""


class RobokassaSettings(models.Model):
    is_enabled = models.BooleanField(default=False)
    merchant_login = models.CharField(max_length=160, blank=True)
    password1 = models.TextField(blank=True)
    password2 = models.TextField(blank=True)
    test_mode = models.BooleanField(default=True)
    hash_algorithm = models.CharField(max_length=32, default="sha256")
    payment_url = models.URLField(default="https://auth.robokassa.ru/Merchant/Index.aspx")
    receipt_enabled = models.BooleanField(default=True)
    receipt_sno = models.CharField(max_length=40, blank=True)
    receipt_tax = models.CharField(max_length=40, default="none")
    receipt_payment_method = models.CharField(max_length=40, default="full_payment")
    receipt_payment_object = models.CharField(max_length=40, default="service")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Настройки Robokassa"
        verbose_name_plural = "Настройки Robokassa"

    def __str__(self):
        return "Robokassa AurumWeb"

    @classmethod
    def load(cls):
        config = cls.objects.order_by("pk").first()
        if config:
            return config
        return cls.objects.create()

    @property
    def has_password1(self):
        return bool(self.get_password1())

    @property
    def has_password2(self):
        return bool(self.get_password2())

    @property
    def configured(self):
        return bool(self.is_enabled and self.merchant_login and self.has_password1 and self.has_password2)

    def get_password1(self):
        return decrypt_value(self.password1)

    def set_password1(self, value):
        self.password1 = encrypt_value(value.strip()) if value else ""

    def get_password2(self):
        return decrypt_value(self.password2)

    def set_password2(self, value):
        self.password2 = encrypt_value(value.strip()) if value else ""
