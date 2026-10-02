from django.conf import settings
from django.db import models
from django.urls import reverse


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Service(TimestampedModel):
    class ServiceType(models.TextChoices):
        PYTHON = "python", "Python-разработка"
        WEBSITE = "website", "Сайт или лендинг"
        SUPPORT = "support", "Поддержка сайтов"
        TELEGRAM = "telegram", "Telegram-боты"
        SEO = "seo", "SEO"
        AUTOMATION = "automation", "Автоматизация"
        CONSULTING = "consulting", "Консалтинг"
        INTEGRATION = "integration", "Бизнес-интеграции"

    title = models.CharField(max_length=180)
    slug = models.SlugField(unique=True)
    service_type = models.CharField(max_length=30, choices=ServiceType.choices)
    short_description = models.CharField(max_length=260)
    full_description = models.TextField(blank=True)
    business_value = models.TextField(blank=True)
    deliverables = models.TextField(blank=True, help_text="По одному пункту на строку.")
    price_prefix = models.CharField(max_length=30, blank=True, default="от", verbose_name="Префикс цены")
    price_amount = models.PositiveIntegerField(null=True, blank=True, verbose_name="Стоимость, ₽")
    price_unit = models.CharField(max_length=80, blank=True, default="за проект", verbose_name="Единица цены")
    price_note = models.CharField(max_length=180, blank=True, verbose_name="Комментарий к цене")
    sort_order = models.PositiveIntegerField(default=100)
    is_featured = models.BooleanField(default=False)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ("sort_order", "title")
        constraints = (
            models.CheckConstraint(
                condition=models.Q(price_amount__isnull=True) | models.Q(price_amount__gt=0),
                name="service_price_positive",
            ),
        )
        indexes = (
            models.Index(fields=("is_published", "service_type", "sort_order"), name="service_public_type_idx"),
            models.Index(fields=("is_published", "is_featured", "sort_order"), name="service_featured_idx"),
        )
        verbose_name = "Услуга"
        verbose_name_plural = "Услуги"

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("content:service-detail", kwargs={"slug": self.slug})

    @property
    def formatted_price_amount(self):
        if self.price_amount is None:
            return ""
        return f"{self.price_amount:,}".replace(",", " ")

    @property
    def price_display(self):
        if self.price_amount is None:
            return "Расчет после разбора"
        parts = []
        if self.price_prefix:
            parts.append(self.price_prefix)
        parts.append(f"{self.formatted_price_amount} ₽")
        if self.price_unit:
            parts.append(self.price_unit)
        return " ".join(parts)


class CaseStudy(TimestampedModel):
    title = models.CharField(max_length=220)
    slug = models.SlugField(unique=True)
    industry = models.CharField(max_length=160, blank=True)
    challenge = models.TextField()
    solution = models.TextField()
    result = models.TextField()
    stack = models.CharField(max_length=260, blank=True)
    is_featured = models.BooleanField(default=False)
    is_published = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ("sort_order", "title")
        verbose_name = "Кейс"
        verbose_name_plural = "Кейсы"

    def __str__(self):
        return self.title


class Testimonial(TimestampedModel):
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="submitted_testimonials",
        verbose_name="Отправил клиент",
    )
    client_name = models.CharField(max_length=160, verbose_name="Имя клиента")
    client_details = models.CharField(
        max_length=180,
        blank=True,
        verbose_name="Компания или подпись",
    )
    project_title = models.CharField(max_length=220, blank=True, verbose_name="Проект")
    text = models.TextField(verbose_name="Текст отзыва")
    publication_consent_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name="Согласие на публикацию",
    )
    is_featured = models.BooleanField(default=False, verbose_name="Показывать первым")
    is_published = models.BooleanField(default=False, verbose_name="Опубликован")
    sort_order = models.PositiveIntegerField(default=100, verbose_name="Порядок")

    class Meta:
        ordering = ("sort_order", "-created_at")
        verbose_name = "Отзыв"
        verbose_name_plural = "Отзывы"

    def __str__(self):
        return self.client_name


class TemplateProduct(TimestampedModel):
    class TemplateType(models.TextChoices):
        WEBSITE = "website", "Шаблон сайта"
        STARTER = "starter", "Стартовый шаблон"
        MODULE = "module", "Модуль"
        CHECKLIST = "checklist", "Чеклист"
        PACKAGE = "package", "Пакет работ"

    title = models.CharField(max_length=180)
    slug = models.SlugField(unique=True)
    template_type = models.CharField(max_length=30, choices=TemplateType.choices)
    category = models.CharField(max_length=120, blank=True, default="", db_index=True)
    industry = models.CharField(max_length=160, blank=True, default="", db_index=True)
    short_description = models.CharField(max_length=260)
    conversion_focus = models.CharField(max_length=260, blank=True, default="")
    contents = models.TextField(blank=True, help_text="Что входит. По одному пункту на строку.")
    implementation_notes = models.TextField(blank=True)
    is_paid_ready = models.BooleanField(default=False, help_text="Можно будет продавать через Robokassa.")
    is_published = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ("sort_order", "title")
        verbose_name = "Шаблон/продукт"
        verbose_name_plural = "Шаблоны и продукты"

    def __str__(self):
        return self.title


class FAQItem(TimestampedModel):
    question = models.CharField(max_length=220)
    answer = models.TextField()
    sort_order = models.PositiveIntegerField(default=100)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ("sort_order", "question")
        verbose_name = "FAQ"
        verbose_name_plural = "FAQ"

    def __str__(self):
        return self.question
