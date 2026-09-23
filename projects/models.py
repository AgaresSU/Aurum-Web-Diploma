from django.conf import settings
from django.db import models


class Project(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Черновик'
        DISCUSSION = 'discussion', 'Обсуждение'
        IN_PROGRESS = 'in_progress', 'В работе'
        REVIEW = 'review', 'Проверка'
        COMPLETED = 'completed', 'Завершён'

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='projects',
        verbose_name='Владелец',
    )
    title = models.CharField(max_length=220, verbose_name='Название проекта')
    service = models.ForeignKey(
        'content.Service',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='projects',
        verbose_name='Услуга',
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT, verbose_name='Статус')
    summary = models.TextField(blank=True, verbose_name='Описание')
    next_action = models.CharField(max_length=240, blank=True, verbose_name='Следующий шаг')
    starts_at = models.DateField(null=True, blank=True, verbose_name='Дата начала')
    due_at = models.DateField(null=True, blank=True, verbose_name='Срок')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        verbose_name = 'проект'
        verbose_name_plural = 'проекты'

    def __str__(self):
        return self.title
