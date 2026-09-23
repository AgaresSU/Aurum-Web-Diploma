from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm, UsernameField
from django.contrib.auth.models import User

from .models import Profile


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

    def clean(self):
        identifier = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if identifier and password:
            user = User.objects.filter(email__iexact=identifier).first()
            username = user.get_username() if user else identifier
            self.user_cache = authenticate(self.request, username=username, password=password)
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)
        return self.cleaned_data


class RegistrationForm(UserCreationForm):
    username = forms.CharField(label="Логин")
    email = forms.EmailField(label="Email для входа и уведомлений")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password1"].label = "Пароль"
        self.fields["password2"].label = "Повторите пароль"

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Пользователь с таким email уже зарегистрирован.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            Profile.objects.get_or_create(user=user)
        return user


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
            "telegram",
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
            "telegram": "Telegram",
            "contract_contact": "Контактное лицо",
            "requisites_comment": "Комментарий к реквизитам",
            "personal_data_consent": "Согласен на обработку данных для счета и договора",
        }
        widgets = {"requisites_comment": forms.Textarea(attrs={"rows": 4})}

    def clean(self):
        cleaned_data = super().clean()
        client_type = cleaned_data.get("client_type")
        if client_type in {Profile.ClientType.SOLE_PROPRIETOR, Profile.ClientType.COMPANY} and not cleaned_data.get("inn"):
            self.add_error("inn", "Для ИП и юрлица нужен ИНН.")
        return cleaned_data
