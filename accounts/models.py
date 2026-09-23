from django.conf import settings
from django.db import models


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    display_name = models.CharField(max_length=160, blank=True, verbose_name='Имя')
    company = models.CharField(max_length=160, blank=True, verbose_name='Компания')
    phone = models.CharField(max_length=40, blank=True, verbose_name='Телефон')
    telegram = models.CharField(max_length=80, blank=True, verbose_name='Telegram')
    bio = models.TextField(blank=True, verbose_name='О себе')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'профиль'
        verbose_name_plural = 'профили'

    def __str__(self):
        return self.display_name or self.user.username
