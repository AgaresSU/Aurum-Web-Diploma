from django import forms


class PublicBriefForm(forms.Form):
    SITE_TYPES = (
        ("Корпоративный сайт", "Корпоративный сайт"),
        ("Посадочная страница", "Посадочная страница"),
        ("Каталог услуг", "Каталог услуг"),
        ("Интернет-витрина", "Интернет-витрина"),
        ("Личный бренд / эксперт", "Личный бренд / эксперт"),
        ("Кабинет клиента / портал", "Кабинет клиента / портал"),
    )
    GOALS = (
        ("Получать заявки", "Получать заявки"),
        ("Упаковать услуги", "Упаковать услуги"),
        ("Подготовить сайт для поиска", "Подготовить сайт для поиска"),
        ("Подключить оплату и счета", "Подключить оплату и счета"),
        ("Автоматизировать продажи", "Автоматизировать продажи"),
        ("Обновить старый сайт", "Обновить старый сайт"),
    )
    INTEGRATIONS = (
        ("Форма заявки", "Форма заявки"),
        ("Личный кабинет", "Личный кабинет"),
        ("Встроенный мессенджер", "Встроенный мессенджер"),
        ("Robokassa", "Платежная страница"),
        ("Telegram-бот", "Telegram-бот"),
        ("Счета и заказы", "Счета и заказы"),
        ("Страницы для поиска", "Страницы для поиска"),
        ("Учет заявок / панель управления", "Учет заявок / панель управления"),
    )
    BUDGETS = (
        ("Нужно оценить", "Нужно оценить"),
        ("До 50 000", "До 50 000"),
        ("50 000 - 120 000", "50 000 - 120 000"),
        ("120 000 - 250 000", "120 000 - 250 000"),
        ("250 000+", "250 000+"),
    )
    TIMEFRAMES = (
        ("Как можно быстрее", "Как можно быстрее"),
        ("1-2 недели", "1-2 недели"),
        ("3-4 недели", "3-4 недели"),
        ("1-2 месяца", "1-2 месяца"),
        ("Пока планируем", "Пока планируем"),
    )

    template_slug = forms.ChoiceField(label="Пример сайта", required=False)
    site_type = forms.ChoiceField(label="Тип сайта", choices=SITE_TYPES)
    industry = forms.CharField(label="Ниша / сфера", max_length=160)
    goal = forms.ChoiceField(label="Главная цель", choices=GOALS)
    integrations = forms.MultipleChoiceField(
        label="Что подключить",
        required=False,
        choices=INTEGRATIONS,
        widget=forms.CheckboxSelectMultiple,
    )
    budget = forms.ChoiceField(label="Бюджетный ориентир", choices=BUDGETS)
    timeframe = forms.ChoiceField(label="Сроки", choices=TIMEFRAMES)
    name = forms.CharField(label="Имя", max_length=160)
    email = forms.EmailField(label="Почта")
    contact = forms.CharField(label="Телефон / Telegram", max_length=160, required=False)
    task = forms.CharField(
        label="Комментарий",
        required=False,
        max_length=5000,
        widget=forms.Textarea(
            attrs={"rows": 5, "placeholder": "Что важно учесть: текущий сайт, материалы, функции, ограничения?"}
        ),
    )
    personal_data_consent = forms.BooleanField(
        label="Согласен на обработку персональных данных для ответа на заявку и подготовки предложения",
        required=True,
        error_messages={"required": "Подтвердите согласие на обработку данных."},
    )

    def __init__(self, *args, templates=None, **kwargs):
        super().__init__(*args, **kwargs)
        template_choices = [("", "Пока не выбрал")]
        for item in templates or []:
            label = item.title
            if item.industry:
                label = f"{item.title} · {item.industry}"
            template_choices.append((item.slug, label))
        self.fields["template_slug"].choices = template_choices
