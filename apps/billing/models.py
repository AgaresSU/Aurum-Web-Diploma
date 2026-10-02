import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        ISSUED = "issued", "Выставлен"
        PAID = "paid", "Оплачен"
        CANCELLED = "cancelled", "Отменен"

    public_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    public_access_expires_at = models.DateTimeField(null=True, blank=True)
    public_access_revoked_at = models.DateTimeField(null=True, blank=True)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices"
    )
    lead = models.ForeignKey("leads.Lead", on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    client_name = models.CharField(max_length=160, blank=True)
    client_email = models.EmailField(blank=True)
    client_requisites = models.JSONField(default=dict, blank=True)
    title = models.CharField(max_length=220)
    description = models.TextField(blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = (models.CheckConstraint(condition=models.Q(amount__gt=0), name="invoice_amount_positive"),)
        indexes = (models.Index(fields=("status", "created_at"), name="invoice_status_created_idx"),)
        verbose_name = "Счет"
        verbose_name_plural = "Счета"

    def __str__(self):
        return f"Счет #{self.pk}: {self.title}"

    @property
    def public_access_deadline(self):
        if self.public_access_expires_at:
            return self.public_access_expires_at
        if self.created_at:
            days = max(int(getattr(settings, "AURUMWEB_PUBLIC_INVOICE_LINK_DAYS", 30)), 1)
            return self.created_at + timedelta(days=days)
        return None

    @property
    def public_access_available(self):
        deadline = self.public_access_deadline
        return (
            self.status in {self.Status.ISSUED, self.Status.PAID}
            and self.public_access_revoked_at is None
            and (deadline is None or deadline > timezone.now())
        )


class Order(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        DISCOVERY = "discovery", "Диагностика"
        PROPOSAL = "proposal", "Предложение"
        IN_PROGRESS = "in_progress", "В работе"
        WAITING_PAYMENT = "waiting_payment", "Ожидает оплату"
        COMPLETED = "completed", "Завершен"
        CANCELLED = "cancelled", "Отменен"

    project = models.ForeignKey(
        "projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
    lead = models.ForeignKey("leads.Lead", on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    invoice = models.OneToOneField(Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="order")
    title = models.CharField(max_length=220)
    client_name = models.CharField(max_length=160, blank=True)
    client_email = models.EmailField(blank=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.DRAFT)
    scope = models.TextField(blank=True)
    internal_notes = models.TextField(blank=True)
    estimated_amount_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    estimated_amount_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    starts_at = models.DateField(null=True, blank=True)
    due_at = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = (
            models.CheckConstraint(
                condition=models.Q(estimated_amount_min__isnull=True) | models.Q(estimated_amount_min__gte=0),
                name="order_estimate_min_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(estimated_amount_max__isnull=True) | models.Q(estimated_amount_max__gte=0),
                name="order_estimate_max_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(estimated_amount_min__isnull=True)
                    | models.Q(estimated_amount_max__isnull=True)
                    | models.Q(estimated_amount_max__gte=models.F("estimated_amount_min"))
                ),
                name="order_estimate_range_valid",
            ),
        )
        indexes = (
            models.Index(fields=("status", "created_at"), name="order_status_created_idx"),
            models.Index(fields=("due_at", "status"), name="order_due_status_idx"),
        )
        verbose_name = "Заказ"
        verbose_name_plural = "Заказы"

    def __str__(self):
        return self.title


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(max_length=220)
    note = models.CharField(max_length=220, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        constraints = (
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="invoice_item_qty_positive"),
            models.CheckConstraint(condition=models.Q(unit_price__gt=0), name="invoice_item_price_positive"),
        )
        verbose_name = "Позиция счета"
        verbose_name_plural = "Позиции счета"

    def __str__(self):
        return self.name

    @property
    def total(self):
        return self.quantity * self.unit_price


class Payment(models.Model):
    class Provider(models.TextChoices):
        ROBOKASSA = "robokassa", "Robokassa"
        MANUAL = "manual", "Ручная отметка"

    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает"
        SUCCEEDED = "succeeded", "Успешен"
        DUPLICATE = "duplicate", "Повторная оплата"
        REVIEW = "review", "Требует проверки"
        FAILED = "failed", "Ошибка"
        CANCELLED = "cancelled", "Отменен"

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    provider = models.CharField(max_length=30, choices=Provider.choices, default=Provider.ROBOKASSA)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    signature_valid = models.BooleanField(default=False)
    provider_payload = models.JSONField(default=dict, blank=True)
    provider_operation_id = models.CharField(max_length=120, blank=True)
    provider_state_code = models.SmallIntegerField(null=True, blank=True)
    last_reconciled_at = models.DateTimeField(null=True, blank=True)
    reconciliation_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = (models.CheckConstraint(condition=models.Q(amount__gt=0), name="payment_amount_positive"),)
        indexes = (
            models.Index(fields=("provider", "status", "created_at"), name="payment_provider_state_idx"),
            models.Index(fields=("status", "paid_at"), name="payment_status_paid_idx"),
        )
        verbose_name = "Платеж"
        verbose_name_plural = "Платежи"

    def __str__(self):
        return f"{self.get_provider_display()} #{self.pk} ({self.get_status_display()})"


class ManagedSite(models.Model):
    class Status(models.TextChoices):
        PLANNING = "planning", "Планирование"
        DEVELOPMENT = "development", "В разработке"
        REVIEW = "review", "На проверке"
        LIVE = "live", "Работает"
        MAINTENANCE = "maintenance", "На поддержке"
        ACTION_REQUIRED = "action_required", "Требует внимания"
        PAUSED = "paused", "Пауза"

    class SupportPlan(models.TextChoices):
        NONE = "none", "Без поддержки"
        BASIC = "basic", "Базовая поддержка"
        GROWTH = "growth", "Развитие и SEO"
        BUSINESS = "business", "Бизнес-сопровождение"

    project = models.ForeignKey(
        "projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="sites"
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    lead = models.ForeignKey(
        "leads.Lead", on_delete=models.SET_NULL, null=True, blank=True, related_name="managed_sites"
    )
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name="managed_sites")
    title = models.CharField(max_length=220)
    url = models.URLField(blank=True)
    domain_name = models.CharField(max_length=180, blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PLANNING)
    support_plan = models.CharField(max_length=32, choices=SupportPlan.choices, default=SupportPlan.BASIC)
    domain_registrar = models.CharField(max_length=160, blank=True)
    domain_expires_at = models.DateField(null=True, blank=True)
    hosting_provider = models.CharField(max_length=160, blank=True)
    hosting_plan = models.CharField(max_length=160, blank=True)
    hosting_expires_at = models.DateField(null=True, blank=True)
    ssl_provider = models.CharField(max_length=160, blank=True, default="Let's Encrypt")
    ssl_expires_at = models.DateField(null=True, blank=True)
    ssl_auto_renew = models.BooleanField(default=True)
    last_backup_at = models.DateTimeField(null=True, blank=True)
    backup_frequency = models.CharField(max_length=120, blank=True, default="Еженедельно")
    last_health_check_at = models.DateTimeField(null=True, blank=True)
    analytics_connected = models.BooleanField(default=False)
    seo_baseline_ready = models.BooleanField(default=False)
    support_until = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("status", "title")
        indexes = (
            models.Index(fields=("status", "title"), name="site_status_title_idx"),
            models.Index(fields=("domain_expires_at",), name="site_domain_expiry_idx"),
            models.Index(fields=("hosting_expires_at",), name="site_hosting_expiry_idx"),
            models.Index(fields=("ssl_expires_at",), name="site_ssl_expiry_idx"),
        )
        verbose_name = "Клиентский сайт"
        verbose_name_plural = "Клиентские сайты"

    def __str__(self):
        return self.title

    def _days_left(self, value):
        if value is None:
            return None
        return (value - timezone.localdate()).days

    def _state_for(self, value):
        days = self._days_left(value)
        if days is None:
            return "unknown"
        if days < 0:
            return "expired"
        if days <= 14:
            return "urgent"
        if days <= 30:
            return "warning"
        return "ok"

    @property
    def domain_days_left(self):
        return self._days_left(self.domain_expires_at)

    @property
    def hosting_days_left(self):
        return self._days_left(self.hosting_expires_at)

    @property
    def ssl_days_left(self):
        return self._days_left(self.ssl_expires_at)

    @property
    def support_days_left(self):
        return self._days_left(self.support_until)

    @property
    def domain_state(self):
        return self._state_for(self.domain_expires_at)

    @property
    def hosting_state(self):
        return self._state_for(self.hosting_expires_at)

    @property
    def ssl_state(self):
        return self._state_for(self.ssl_expires_at)

    @property
    def support_state(self):
        return self._state_for(self.support_until)
