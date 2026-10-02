from django import forms


class PublicMessageForm(forms.Form):
    body = forms.CharField(
        label="Сообщение",
        max_length=4000,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "Напишите ответ или уточнение по задаче",
            }
        ),
    )
    personal_data_consent = forms.BooleanField(
        label="Согласен на обработку данных в рамках переписки по заявке",
        required=True,
        error_messages={"required": "Подтвердите согласие на обработку данных."},
    )
