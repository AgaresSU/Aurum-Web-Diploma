from django import forms

from .models import Testimonial


class TestimonialSubmissionForm(forms.ModelForm):
    publication_consent = forms.BooleanField(
        label="Разрешаю опубликовать этот отзыв на сайте",
        required=True,
        error_messages={"required": "Подтвердите согласие на публикацию отзыва."},
    )

    class Meta:
        model = Testimonial
        fields = ("client_name", "client_details", "project_title", "text")
        labels = {
            "client_name": "Как вас подписать",
            "client_details": "Компания или подпись",
            "project_title": "О каком проекте отзыв",
            "text": "Ваш отзыв",
        }
        widgets = {
            "client_name": forms.TextInput(attrs={"placeholder": "Например: Алексей К."}),
            "client_details": forms.TextInput(attrs={"placeholder": "Например: владелец студии — необязательно"}),
            "project_title": forms.TextInput(attrs={"placeholder": "Например: сайт компании — необязательно"}),
            "text": forms.Textarea(
                attrs={
                    "rows": 6,
                    "placeholder": "Расскажите простыми словами, как прошла работа и что получилось.",
                }
            ),
        }

    def clean_client_name(self):
        return self.cleaned_data["client_name"].strip()

    def clean_text(self):
        text = self.cleaned_data["text"].strip()
        if len(text) < 20:
            raise forms.ValidationError("Напишите отзыв чуть подробнее — хотя бы 20 символов.")
        return text
