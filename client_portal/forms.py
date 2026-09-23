from django import forms


class ClientLeadForm(forms.Form):
    template_slug = forms.CharField(required=False, widget=forms.HiddenInput)
    service_type = forms.ChoiceField(
        label="Направление",
        choices=(
            ("Сайт под ключ", "Сайт под ключ"),
            ("Поддержка сайта", "Поддержка сайта"),
            ("Python-программа", "Python-программа"),
            ("Telegram-бот", "Telegram-бот"),
            ("Сайт на базе шаблона", "Сайт на базе шаблона"),
            ("Интеграции для сайта", "Интеграции для сайта"),
            ("Консультация по проекту", "Консультация по проекту"),
        ),
    )
    subject = forms.CharField(label="Тема", max_length=220)
    task = forms.CharField(
        label="Описание задачи",
        max_length=8000,
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": "Что нужно получить на выходе?"}),
    )


class ClientMessageForm(forms.Form):
    body = forms.CharField(
        label="Сообщение",
        max_length=4000,
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Напишите сообщение менеджеру"}),
    )
