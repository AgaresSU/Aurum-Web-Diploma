from django.conf import settings
from django.db import models


class Profile(models.Model):
    class ClientType(models.TextChoices):
        INDIVIDUAL = "individual", "Физлицо"
        SOLE_PROPRIETOR = "sole_proprietor", "ИП"
        COMPANY = "company", "Юрлицо"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    display_name = models.CharField(max_length=160, blank=True)
    company = models.CharField(max_length=160, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    telegram = models.CharField(max_length=80, blank=True)
    bio = models.TextField(blank=True)
    client_type = models.CharField(
        max_length=30,
        choices=ClientType.choices,
        default=ClientType.INDIVIDUAL,
    )
    legal_name = models.CharField(max_length=220, blank=True)
    inn = models.CharField(max_length=12, blank=True)
    kpp = models.CharField(max_length=9, blank=True)
    ogrn = models.CharField(max_length=15, blank=True)
    billing_address = models.CharField(max_length=260, blank=True)
    contract_contact = models.CharField(max_length=160, blank=True)
    requisites_comment = models.TextField(blank=True)
    personal_data_consent = models.BooleanField(default=False)
    telegram_chat_id = models.CharField(max_length=80, blank=True)
    telegram_notifications_enabled = models.BooleanField(default=False)
    telegram_link_code = models.CharField(max_length=24, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Профиль"
        verbose_name_plural = "Профили"

    def __str__(self):
        return self.display_name or self.user.get_full_name() or self.user.username

    @property
    def telegram_linked(self):
        return bool(self.telegram_chat_id)

    @property
    def payer_name(self):
        return self.legal_name or self.company or self.display_name or self.user.get_full_name() or self.user.username

    @property
    def requisites_complete(self):
        if self.client_type in {self.ClientType.SOLE_PROPRIETOR, self.ClientType.COMPANY} and not self.inn:
            return False
        return bool(self.payer_name and self.personal_data_consent)
