from django import forms
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm, UsernameField
from django.contrib.auth.models import User

from .models import Profile


def _normalized_registration_email_domains():
    return {
        str(domain).strip().lower().lstrip("@").rstrip(".")
        for domain in getattr(settings, "AURUMWEB_REGISTRATION_EMAIL_ALLOWED_DOMAINS", ())
        if str(domain).strip()
    }


def _normalized_registration_email_suffixes():
    suffixes = []
    for suffix in getattr(settings, "AURUMWEB_REGISTRATION_EMAIL_ALLOWED_SUFFIXES", ()):
        value = str(suffix).strip().lower().rstrip(".")
        if not value:
            continue
        suffixes.append(value if value.startswith(".") else f".{value}")
    return tuple(suffixes)


def is_registration_email_domain_allowed(email):
    domain = email.rsplit("@", 1)[-1].strip().lower().rstrip(".")
    allowed_domains = _normalized_registration_email_domains()
    allowed_suffixes = _normalized_registration_email_suffixes()
    return domain in allowed_domains or any(domain.endswith(suffix) for suffix in allowed_suffixes)


class EmailAuthenticationForm(AuthenticationForm):
    username = UsernameField(
        label="Email или логин",
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username"}),
    )
    password = forms.CharField(
        label="Пароль",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    def __init__(self, *args, **kwargs):
        self.inactive_user = None
        super().__init__(*args, **kwargs)

    def clean(self):
        identifier = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")

        if identifier is not None and password:
            auth_username = identifier
            user = User._default_manager.filter(email__iexact=identifier).first()
            if not user:
                user = User._default_manager.filter(username__iexact=identifier).first()

            if user:
                auth_username = user.get_username()
                if not user.is_active and user.check_password(password):
                    self.inactive_user = user
                    raise forms.ValidationError(
                        "Email еще не подтвержден. Введите код из письма или запросите новый код.",
                        code="inactive",
                    )

            self.user_cache = authenticate(self.request, username=auth_username, password=password)
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data


class TotpAuthenticationForm(forms.Form):
    code = forms.CharField(
        label="Код из приложения или резервный код",
        max_length=16,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "placeholder": "000000 или XXXX-XXXX",
            }
        ),
    )

    def clean_code(self):
        code = "".join(self.cleaned_data["code"].split()).upper()
        compact = code.replace("-", "")
        is_totp = compact.isdigit() and len(compact) == 6
        is_backup_code = compact.isalnum() and len(compact) == 8
        if not is_totp and not is_backup_code:
            raise forms.ValidationError("Введите 6-значный код из приложения или резервный код формата XXXX-XXXX.")
        return code


class RegistrationForm(UserCreationForm):
    username = forms.CharField(label="Логин")
    email = forms.EmailField(label="Российский email для входа и уведомлений")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.existing_user = None
        self.fields["password1"].label = "Пароль"
        self.fields["password2"].label = "Повторите пароль"

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if not is_registration_email_domain_allowed(email):
            raise forms.ValidationError(
                "Для регистрации сейчас доступны только российские email-домены. "
                "Используйте почту на .ru, .рф, .su или российский почтовый сервис."
            )
        self.existing_user = User.objects.filter(email__iexact=email).first()
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.is_active = False
        if commit:
            user.save()
            Profile.objects.get_or_create(user=user)
        return user


class EmailVerificationForm(forms.Form):
    code = forms.CharField(
        label="Код из письма",
        min_length=6,
        max_length=6,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "placeholder": "000000",
            }
        ),
    )

    def clean_code(self):
        code = "".join(self.cleaned_data["code"].split())
        if not code.isdigit() or len(code) != 6:
            raise forms.ValidationError("Введите 6 цифр из письма.")
        return code


class ClientRequisitesForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = (
            "client_type",
            "legal_name",
            "company",
            "inn",
            "kpp",
            "ogrn",
            "billing_address",
            "phone",
            "telegram_username",
            "telegram_notifications_enabled",
            "contract_contact",
            "requisites_comment",
            "personal_data_consent",
        )
        labels = {
            "client_type": "Тип плательщика",
            "legal_name": "ФИО или юр. название для счета",
            "company": "Компания / бренд",
            "inn": "ИНН",
            "kpp": "КПП",
            "ogrn": "ОГРН / ОГРНИП",
            "billing_address": "Адрес для договора",
            "phone": "Телефон",
            "telegram_username": "Telegram",
            "telegram_notifications_enabled": "Получать уведомления в Telegram",
            "contract_contact": "Контактное лицо",
            "requisites_comment": "Комментарий к реквизитам",
            "personal_data_consent": "Согласен на обработку данных для счета и договора",
        }
        widgets = {
            "requisites_comment": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        profile = self.instance
        field = self.fields["telegram_notifications_enabled"]
        if not getattr(profile, "telegram_chat_id", ""):
            field.disabled = True
            field.initial = False
            field.help_text = "Сначала привяжите Telegram через код ниже. Chat ID сохранится автоматически."
        else:
            field.help_text = "Можно выключить, если не хотите получать сообщения о проекте в Telegram."

    def clean(self):
        cleaned_data = super().clean()
        client_type = cleaned_data.get("client_type")
        inn = (cleaned_data.get("inn") or "").strip()
        if client_type in {Profile.ClientType.SOLE_PROPRIETOR, Profile.ClientType.COMPANY} and not inn:
            self.add_error("inn", "Для ИП и юрлица нужен ИНН.")
        if cleaned_data.get("telegram_notifications_enabled") and not self.instance.telegram_chat_id:
            self.add_error(
                "telegram_notifications_enabled", "Сначала привяжите Telegram через код из личного кабинета."
            )
        return cleaned_data
