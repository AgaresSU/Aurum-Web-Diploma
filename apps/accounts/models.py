import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone
from django.utils.crypto import get_random_string

from .crypto import decrypt_value, encrypt_value


class Profile(models.Model):
    class Role(models.TextChoices):
        CLIENT = "client", "Клиент"
        MANAGER = "manager", "Менеджер"
        ADMIN = "admin", "Администратор"

    class ClientType(models.TextChoices):
        INDIVIDUAL = "individual", "Физлицо"
        SOLE_PROPRIETOR = "sole_proprietor", "ИП"
        COMPANY = "company", "Юрлицо"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CLIENT)
    company = models.CharField(max_length=160, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    telegram_username = models.CharField(max_length=80, blank=True)
    telegram_chat_id = models.CharField(max_length=80, blank=True)
    telegram_notifications_enabled = models.BooleanField(default=False)
    telegram_link_code = models.CharField(max_length=24, blank=True, db_index=True)
    telegram_link_code_created_at = models.DateTimeField(null=True, blank=True)
    client_type = models.CharField(max_length=30, choices=ClientType.choices, default=ClientType.INDIVIDUAL)
    legal_name = models.CharField(max_length=220, blank=True)
    inn = models.CharField(max_length=12, blank=True)
    kpp = models.CharField(max_length=9, blank=True)
    ogrn = models.CharField(max_length=15, blank=True)
    billing_address = models.CharField(max_length=260, blank=True)
    contract_contact = models.CharField(max_length=160, blank=True)
    requisites_comment = models.TextField(blank=True)
    personal_data_consent = models.BooleanField(default=False)
    totp_enabled = models.BooleanField(default=False)
    totp_secret = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Профиль"
        verbose_name_plural = "Профили"

    def __str__(self):
        return f"{self.user} ({self.get_role_display()})"

    def get_totp_secret(self):
        return decrypt_value(self.totp_secret)

    def set_totp_secret(self, secret):
        self.totp_secret = encrypt_value(secret)

    def refresh_telegram_link_code(self):
        while True:
            code = get_random_string(10, allowed_chars="ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
            exists = Profile.objects.filter(telegram_link_code=code).exclude(pk=self.pk).exists()
            if not exists:
                break
        self.telegram_link_code = code
        self.telegram_link_code_created_at = timezone.now()
        return code

    def ensure_telegram_link_code(self):
        if not self.telegram_link_code:
            self.refresh_telegram_link_code()
            self.save(update_fields=("telegram_link_code", "telegram_link_code_created_at"))
        return self.telegram_link_code

    @property
    def telegram_linked(self):
        return bool(self.telegram_chat_id)

    @property
    def payer_name(self):
        return self.legal_name or self.company or self.user.get_full_name() or self.user.username

    @property
    def requisites_complete(self):
        if not self.payer_name:
            return False
        if self.client_type in {self.ClientType.SOLE_PROPRIETOR, self.ClientType.COMPANY} and not self.inn:
            return False
        return bool(self.personal_data_consent)

    def requisites_payload(self):
        return {
            "client_type": self.client_type,
            "client_type_display": self.get_client_type_display(),
            "payer_name": self.payer_name,
            "company": self.company,
            "phone": self.phone,
            "telegram_username": self.telegram_username,
            "telegram_notifications_enabled": self.telegram_notifications_enabled,
            "legal_name": self.legal_name,
            "inn": self.inn,
            "kpp": self.kpp,
            "ogrn": self.ogrn,
            "billing_address": self.billing_address,
            "contract_contact": self.contract_contact,
            "requisites_comment": self.requisites_comment,
            "personal_data_consent": self.personal_data_consent,
            "complete": self.requisites_complete,
        }


class BackupCode(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="backup_codes")
    code_hash = models.CharField(max_length=128)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Резервный код"
        verbose_name_plural = "Резервные коды"

    def __str__(self):
        status = "использован" if self.used_at else "активен"
        return f"{self.user} · {status}"


class EmailVerificationCode(models.Model):
    class Purpose(models.TextChoices):
        EMAIL_VERIFY = "email_verify", "Подтверждение email"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="email_codes")
    email = models.EmailField()
    purpose = models.CharField(max_length=40, choices=Purpose.choices)
    code_hash = models.CharField(max_length=128)
    attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Код подтверждения email"
        verbose_name_plural = "Коды подтверждения email"
        indexes = [
            models.Index(fields=("user", "purpose", "used_at", "created_at")),
            models.Index(fields=("email", "purpose", "created_at")),
        ]

    def __str__(self):
        status = "использован" if self.used_at else "активен"
        return f"{self.email} · {self.get_purpose_display()} · {status}"

    @classmethod
    def issue(cls, user, purpose, email=None, ttl_minutes=20):
        now = timezone.now()
        cls.objects.filter(user=user, purpose=purpose, used_at__isnull=True).update(used_at=now)
        code = f"{secrets.randbelow(1_000_000):06d}"
        verification_code = cls.objects.create(
            user=user,
            email=email or user.email,
            purpose=purpose,
            code_hash=make_password(code),
            expires_at=now + timedelta(minutes=ttl_minutes),
        )
        return verification_code, code

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    def verify(self, code, max_attempts=5):
        if self.used_at:
            return False, "used"
        if self.is_expired:
            return False, "expired"
        if self.attempts >= max_attempts:
            return False, "locked"

        if not check_password(code, self.code_hash):
            self.attempts += 1
            self.save(update_fields=("attempts",))
            if self.attempts >= max_attempts:
                return False, "locked"
            return False, "invalid"

        self.used_at = timezone.now()
        self.save(update_fields=("used_at",))
        return True, "ok"
