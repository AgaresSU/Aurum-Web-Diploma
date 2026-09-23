from django.db import models
from django.templatetags.static import static
from django.urls import reverse


class Service(models.Model):
    title = models.CharField(max_length=180, verbose_name='Название')
    slug = models.SlugField(unique=True)
    short_description = models.CharField(max_length=260, verbose_name='Краткое описание')
    full_description = models.TextField(blank=True, verbose_name='Полное описание')
    sort_order = models.PositiveIntegerField(default=100, verbose_name='Порядок')
    is_published = models.BooleanField(default=True, verbose_name='Опубликовано')

    class Meta:
        ordering = ['sort_order', 'title']
        verbose_name = 'услуга'
        verbose_name_plural = 'услуги'

    def __str__(self):
        return self.title


class Work(models.Model):
    title = models.CharField(max_length=220, verbose_name='Название')
    slug = models.SlugField(unique=True)
    category = models.CharField(max_length=120, verbose_name='Категория')
    short_description = models.CharField(max_length=260, verbose_name='Краткое описание')
    description = models.TextField(blank=True, verbose_name='Описание')
    features = models.TextField(blank=True, help_text='По одному пункту на строку.', verbose_name='Что сделано')
    preview_image = models.ImageField(upload_to='works/', blank=True, null=True, verbose_name='Изображение')
    preview_static = models.CharField(max_length=220, blank=True, verbose_name='Изображение из static')
    demo_link = models.URLField(blank=True, verbose_name='Ссылка на сайт')
    demo_static = models.CharField(max_length=220, blank=True, verbose_name='Демо из static')
    is_published = models.BooleanField(default=True, verbose_name='Опубликовано')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Добавлено')

    class Meta:
        ordering = ['-created_at', 'title']
        verbose_name = 'пример работы'
        verbose_name_plural = 'примеры работ'

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('work_detail', args=[self.slug])

    @property
    def preview_url(self):
        if self.preview_image:
            return self.preview_image.url
        if self.preview_static:
            return static(self.preview_static)
        return ''

    @property
    def demo_url(self):
        if self.demo_link:
            return self.demo_link
        if self.demo_static:
            return static(self.demo_static)
        return ''

    @property
    def feature_list(self):
        return [item.strip() for item in self.features.splitlines() if item.strip()]
