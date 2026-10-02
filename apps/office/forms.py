from django import forms

from apps.accounts.totp import verify_totp
from apps.billing.models import Invoice, ManagedSite, Order
from apps.content.models import Service
from apps.integrations.models import QuickConsultationBotSettings, RobokassaSettings, TelegramBotSettings
from apps.leads.models import Lead, QuickConsultation


class SecurityPasswordForm(forms.Form):
    password = forms.CharField(
        label="Текущий пароль",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not self.user or not self.user.check_password(password):
            raise forms.ValidationError("Пароль не подошел.")
        return password


class TotpSetupConfirmForm(SecurityPasswordForm):
    code = forms.CharField(
        label="Код из приложения",
        max_length=12,
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric"}),
    )

    def __init__(self, *args, secret="", **kwargs):
        self.secret = secret
        super().__init__(*args, **kwargs)

    def clean_code(self):
        code = self.cleaned_data["code"]
        if not self.secret or not verify_totp(self.secret, code):
            raise forms.ValidationError("Код не подошел. Проверьте ключ в приложении Authenticator.")
        return code


class TelegramIntegrationForm(SecurityPasswordForm):
    is_enabled = forms.BooleanField(label="Включить уведомления", required=False)
    token_mask = forms.CharField(
        label="Сохраненный ключ бота",
        required=False,
        disabled=True,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    new_bot_token = forms.CharField(
        label="Новый ключ бота",
        required=False,
        strip=True,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "placeholder": "Вставьте ключ бота"}),
        help_text="Оставьте пустым, если сохраненный ключ менять не нужно.",
    )
    bot_username = forms.CharField(
        label="Имя бота",
        required=False,
        max_length=80,
        widget=forms.TextInput(attrs={"placeholder": "Например: AurumWebBot"}),
        help_text="Нужно для кнопки подключения клиента. Можно нажать “Проверить бота”, и имя заполнится автоматически.",
    )
    admin_chat_id = forms.CharField(
        label="Чат для уведомлений",
        required=False,
        max_length=80,
        widget=forms.TextInput(attrs={"placeholder": "Например: 123456789"}),
        help_text="Нужен для уведомлений менеджеру. Его можно получить кнопкой ниже после сообщения боту.",
    )
    webhook_secret_mask = forms.CharField(
        label="Сохраненный ключ уведомлений",
        required=False,
        disabled=True,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    new_webhook_secret = forms.CharField(
        label="Новый ключ уведомлений",
        required=False,
        strip=True,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password", "placeholder": "Любая длинная случайная строка"}
        ),
        help_text="Нужен, чтобы Telegram принимал только настоящие уведомления от сайта.",
    )
    webhook_url = forms.URLField(
        label="Адрес уведомлений",
        required=False,
        widget=forms.URLInput(attrs={"placeholder": "https://domain.ru/integrations/telegram/webhook/"}),
        help_text="Можно оставить пустым до подключения домена. После публикации укажите защищенный адрес уведомлений.",
    )

    def __init__(self, *args, user=None, config=None, **kwargs):
        self.config = config or TelegramBotSettings()
        initial = kwargs.pop("initial", {})
        initial.update(
            {
                "is_enabled": self.config.is_enabled,
                "token_mask": "************" if self.config.has_bot_token else "не сохранен",
                "bot_username": self.config.bot_username,
                "admin_chat_id": self.config.admin_chat_id,
                "webhook_secret_mask": "************" if self.config.has_webhook_secret else "не сохранен",
                "webhook_url": self.config.webhook_url,
            }
        )
        super().__init__(*args, user=user, initial=initial, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        wants_enabled = cleaned_data.get("is_enabled")
        has_token = bool(cleaned_data.get("new_bot_token") or self.config.has_bot_token)
        has_chat_id = bool(cleaned_data.get("admin_chat_id", "").strip())
        if wants_enabled and not has_token:
            self.add_error("new_bot_token", "Чтобы включить уведомления, сначала сохраните ключ бота.")
        if wants_enabled and not has_chat_id:
            self.add_error("admin_chat_id", "Чтобы включить уведомления, укажите чат для сообщений.")
        return cleaned_data

    def save(self):
        self.config.is_enabled = self.cleaned_data.get("is_enabled", False)
        self.config.bot_username = self.cleaned_data.get("bot_username", "").strip().removeprefix("@")
        self.config.admin_chat_id = self.cleaned_data.get("admin_chat_id", "").strip()
        self.config.webhook_url = self.cleaned_data.get("webhook_url", "").strip()
        if self.cleaned_data.get("new_bot_token"):
            self.config.set_bot_token(self.cleaned_data["new_bot_token"])
        if self.cleaned_data.get("new_webhook_secret"):
            self.config.set_webhook_secret(self.cleaned_data["new_webhook_secret"])
        self.config.save()
        return self.config


class QuickConsultationTelegramForm(TelegramIntegrationForm):
    def __init__(self, *args, user=None, config=None, **kwargs):
        super().__init__(*args, user=user, config=config or QuickConsultationBotSettings(), **kwargs)
        self.fields["bot_username"].label = "Имя бота быстрых консультаций"
        self.fields["bot_username"].help_text = (
            "Например: AurumWebFast_bot. Используется для ссылки, которую можно отправить клиенту."
        )
        self.fields["new_bot_token"].help_text = "Ключ отдельного Telegram-бота для быстрых консультаций."
        self.fields["admin_chat_id"].help_text = "Чат для уведомлений о новых быстрых консультациях."
        self.fields["webhook_url"].help_text = "Защищенный адрес уведомлений отдельного бота."


class RobokassaIntegrationForm(SecurityPasswordForm):
    HASH_CHOICES = (
        ("sha256", "SHA-256"),
        ("sha512", "SHA-512"),
        ("md5", "MD5"),
    )
    TAX_CHOICES = (
        ("none", "Без НДС"),
        ("vat0", "НДС 0%"),
        ("vat5", "НДС 5%"),
        ("vat7", "НДС 7%"),
        ("vat10", "НДС 10%"),
        ("vat20", "НДС 20%"),
    )
    PAYMENT_METHOD_CHOICES = (
        ("full_payment", "Полный расчет"),
        ("prepayment", "Предоплата"),
        ("advance", "Аванс"),
    )
    PAYMENT_OBJECT_CHOICES = (
        ("service", "Услуга"),
        ("commodity", "Товар"),
        ("work", "Работа"),
        ("payment", "Платеж"),
    )

    is_enabled = forms.BooleanField(label="Использовать настройки из админки", required=False)
    merchant_login = forms.CharField(
        label="Логин магазина",
        required=False,
        max_length=160,
        widget=forms.TextInput(attrs={"placeholder": "Логин магазина Robokassa"}),
    )
    password1_mask = forms.CharField(
        label="Пароль оплаты",
        required=False,
        disabled=True,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    new_password1 = forms.CharField(
        label="Новый пароль оплаты",
        required=False,
        strip=True,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "placeholder": "Вставьте пароль оплаты"}),
        help_text="Оставьте пустым, если сохраненный пароль менять не нужно.",
    )
    password2_mask = forms.CharField(
        label="Пароль уведомлений",
        required=False,
        disabled=True,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    new_password2 = forms.CharField(
        label="Новый пароль уведомлений",
        required=False,
        strip=True,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password", "placeholder": "Вставьте пароль уведомлений"}
        ),
        help_text="Оставьте пустым, если сохраненный пароль менять не нужно.",
    )
    test_mode = forms.BooleanField(label="Тестовый режим Robokassa", required=False)
    hash_algorithm = forms.ChoiceField(label="Алгоритм подписи", choices=HASH_CHOICES, required=False)
    payment_url = forms.URLField(
        label="Адрес платежной страницы",
        required=False,
        widget=forms.URLInput(attrs={"placeholder": "https://auth.robokassa.ru/Merchant/Index.aspx"}),
    )
    receipt_enabled = forms.BooleanField(label="Передавать чек самозанятого", required=False)
    receipt_sno = forms.CharField(
        label="Система налогообложения",
        required=False,
        max_length=40,
        widget=forms.TextInput(attrs={"placeholder": "Оставьте пустым, если СМЗ не требует"}),
    )
    receipt_tax = forms.ChoiceField(label="Налог в позициях чека", choices=TAX_CHOICES, required=False)
    receipt_payment_method = forms.ChoiceField(label="Способ расчета", choices=PAYMENT_METHOD_CHOICES, required=False)
    receipt_payment_object = forms.ChoiceField(label="Предмет расчета", choices=PAYMENT_OBJECT_CHOICES, required=False)

    def __init__(self, *args, user=None, config=None, **kwargs):
        self.config = config or RobokassaSettings()
        initial = kwargs.pop("initial", {})
        initial.update(
            {
                "is_enabled": self.config.is_enabled,
                "merchant_login": self.config.merchant_login,
                "password1_mask": "************" if self.config.has_password1 else "не сохранен",
                "password2_mask": "************" if self.config.has_password2 else "не сохранен",
                "test_mode": self.config.test_mode,
                "hash_algorithm": self.config.hash_algorithm or "sha256",
                "payment_url": self.config.payment_url,
                "receipt_enabled": self.config.receipt_enabled,
                "receipt_sno": self.config.receipt_sno,
                "receipt_tax": self.config.receipt_tax or "none",
                "receipt_payment_method": self.config.receipt_payment_method or "full_payment",
                "receipt_payment_object": self.config.receipt_payment_object or "service",
            }
        )
        super().__init__(*args, user=user, initial=initial, **kwargs)

    def clean_hash_algorithm(self):
        return (self.cleaned_data.get("hash_algorithm") or "sha256").lower()

    def clean_payment_url(self):
        return (self.cleaned_data.get("payment_url") or "https://auth.robokassa.ru/Merchant/Index.aspx").strip()

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("is_enabled"):
            return cleaned_data
        has_password1 = bool(cleaned_data.get("new_password1") or self.config.has_password1)
        has_password2 = bool(cleaned_data.get("new_password2") or self.config.has_password2)
        if not cleaned_data.get("merchant_login", "").strip():
            self.add_error("merchant_login", "Чтобы включить Robokassa, укажите логин магазина.")
        if not has_password1:
            self.add_error("new_password1", "Чтобы включить Robokassa, сохраните пароль оплаты.")
        if not has_password2:
            self.add_error("new_password2", "Чтобы включить Robokassa, сохраните пароль уведомлений.")
        return cleaned_data

    def save(self):
        self.config.is_enabled = self.cleaned_data.get("is_enabled", False)
        self.config.merchant_login = self.cleaned_data.get("merchant_login", "").strip()
        self.config.test_mode = self.cleaned_data.get("test_mode", False)
        self.config.hash_algorithm = self.cleaned_data.get("hash_algorithm") or "sha256"
        self.config.payment_url = (
            self.cleaned_data.get("payment_url") or "https://auth.robokassa.ru/Merchant/Index.aspx"
        )
        self.config.receipt_enabled = self.cleaned_data.get("receipt_enabled", False)
        self.config.receipt_sno = self.cleaned_data.get("receipt_sno", "").strip()
        self.config.receipt_tax = self.cleaned_data.get("receipt_tax") or "none"
        self.config.receipt_payment_method = self.cleaned_data.get("receipt_payment_method") or "full_payment"
        self.config.receipt_payment_object = self.cleaned_data.get("receipt_payment_object") or "service"
        if self.cleaned_data.get("new_password1"):
            self.config.set_password1(self.cleaned_data["new_password1"])
        if self.cleaned_data.get("new_password2"):
            self.config.set_password2(self.cleaned_data["new_password2"])
        self.config.save()
        return self.config


class LeadUpdateForm(forms.ModelForm):
    class Meta:
        model = Lead
        fields = ("status", "service_type", "subject", "name", "email", "task")
        widgets = {
            "task": forms.Textarea(attrs={"rows": 6}),
        }


class QuickConsultationForm(forms.ModelForm):
    class Meta:
        model = QuickConsultation
        fields = ("status", "quoted_amount", "manager_response", "internal_note")
        labels = {
            "status": "Статус",
            "quoted_amount": "Стоимость консультации, ₽",
            "manager_response": "Ответ клиенту",
            "internal_note": "Внутренняя заметка",
        }
        help_texts = {
            "quoted_amount": "Эта сумма попадет в счет. Клиент сам сумму не вводит.",
            "manager_response": "Текст можно отправить клиенту в Telegram или использовать как описание счета.",
            "internal_note": "Видно только в Office.",
        }
        widgets = {
            "manager_response": forms.Textarea(attrs={"rows": 5}),
            "internal_note": forms.Textarea(attrs={"rows": 4}),
        }

    def clean_quoted_amount(self):
        amount = self.cleaned_data.get("quoted_amount")
        if amount is not None and amount <= 0:
            raise forms.ValidationError("Сумма должна быть больше нуля.")
        return amount


class LeadQualificationForm(forms.Form):
    INTEGRATION_CHOICES = (
        ("Форма заявки", "Форма заявки"),
        ("Личный кабинет", "Личный кабинет"),
        ("Встроенный мессенджер", "Встроенный мессенджер"),
        ("Robokassa", "Robokassa"),
        ("Telegram-бот", "Telegram-бот"),
        ("Счета и заказы", "Счета и заказы"),
        ("SEO-страницы", "SEO-страницы"),
        ("CRM / админка", "CRM / админка"),
    )

    project_type = forms.CharField(label="Тип сайта", max_length=160, required=False)
    industry = forms.CharField(label="Ниша", max_length=160, required=False)
    goal = forms.CharField(label="Цель", max_length=220, required=False)
    budget = forms.CharField(label="Бюджет", max_length=120, required=False)
    timeframe = forms.CharField(label="Сроки", max_length=120, required=False)
    contact = forms.CharField(label="Контакт", max_length=160, required=False)
    integrations = forms.MultipleChoiceField(
        label="Интеграции",
        required=False,
        choices=INTEGRATION_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )
    has_domain = forms.BooleanField(label="Домен есть", required=False)
    has_hosting = forms.BooleanField(label="Хостинг есть", required=False)
    needs_design = forms.BooleanField(label="Нужна дизайн-адаптация", required=False)
    needs_content = forms.BooleanField(label="Нужно подготовить контент", required=False)
    needs_seo = forms.BooleanField(label="Нужна SEO-структура", required=False)
    needs_robokassa = forms.BooleanField(label="Нужна Robokassa", required=False)
    needs_telegram = forms.BooleanField(label="Нужен Telegram-бот", required=False)
    needs_client_portal = forms.BooleanField(label="Нужен личный кабинет", required=False)
    needs_billing = forms.BooleanField(label="Нужны счета и заказы", required=False)
    needs_admin = forms.BooleanField(label="Нужна CRM / админка", required=False)
    needs_support = forms.BooleanField(label="Нужно сопровождение", required=False)
    manager_notes = forms.CharField(
        label="Заметки менеджера",
        required=False,
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 4}),
    )


class InvoiceCreateForm(forms.ModelForm):
    item_name = forms.CharField(
        label="Позиция для чека",
        max_length=220,
        required=False,
        help_text="Например: Консультация, сайт под ключ, поддержка сайта.",
    )
    item_note = forms.CharField(
        label="Короткое примечание к позиции",
        max_length=220,
        required=False,
        help_text="Например: тестовый платеж, продвижение сайта, построение сайта.",
    )

    class Meta:
        model = Invoice
        fields = ("title", "description", "amount", "due_date")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean_item_name(self):
        return (self.cleaned_data.get("item_name") or self.cleaned_data.get("title") or "Услуга AurumWeb").strip()

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is not None and amount <= 0:
            raise forms.ValidationError("Сумма счета должна быть больше нуля.")
        return amount

    def clean_item_note(self):
        return (self.cleaned_data.get("item_note") or "").strip()


class ServicePriceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = (
            "price_prefix",
            "price_amount",
            "price_unit",
            "price_note",
            "is_featured",
            "is_published",
            "sort_order",
        )
        labels = {
            "price_prefix": "Префикс",
            "price_amount": "Стоимость, ₽",
            "price_unit": "Единица цены",
            "price_note": "Комментарий под ценой",
            "is_featured": "Показывать на главной",
            "is_published": "Опубликовано",
            "sort_order": "Порядок",
        }
        help_texts = {
            "price_prefix": "Например: от, фиксировано, ежемесячно. Можно оставить пустым.",
            "price_amount": "Если оставить пустым, на сайте будет показано: “Расчет после разбора”.",
            "price_unit": "Например: за проект, в месяц, за консультацию.",
            "price_note": "Короткое пояснение для клиента: когда сумма уточняется и что влияет на цену.",
        }

    def clean_price_amount(self):
        amount = self.cleaned_data.get("price_amount")
        if amount is not None and amount <= 0:
            raise forms.ValidationError("Стоимость должна быть больше нуля.")
        return amount


class OrderUpdateForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = (
            "status",
            "scope",
            "internal_notes",
            "estimated_amount_min",
            "estimated_amount_max",
            "starts_at",
            "due_at",
        )
        widgets = {
            "scope": forms.Textarea(attrs={"rows": 5}),
            "internal_notes": forms.Textarea(attrs={"rows": 5}),
            "starts_at": forms.DateInput(attrs={"type": "date"}),
            "due_at": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        amount_min = cleaned_data.get("estimated_amount_min")
        amount_max = cleaned_data.get("estimated_amount_max")
        if amount_min is not None and amount_min < 0:
            self.add_error("estimated_amount_min", "Сумма не может быть отрицательной.")
        if amount_max is not None and amount_max < 0:
            self.add_error("estimated_amount_max", "Сумма не может быть отрицательной.")
        if amount_min is not None and amount_max is not None and amount_max < amount_min:
            self.add_error("estimated_amount_max", "Верхняя граница должна быть не меньше нижней.")
        return cleaned_data


class ManagedSiteUpdateForm(forms.ModelForm):
    class Meta:
        model = ManagedSite
        fields = (
            "title",
            "user",
            "lead",
            "order",
            "status",
            "support_plan",
            "url",
            "domain_name",
            "domain_registrar",
            "domain_expires_at",
            "hosting_provider",
            "hosting_plan",
            "hosting_expires_at",
            "ssl_provider",
            "ssl_expires_at",
            "ssl_auto_renew",
            "last_backup_at",
            "backup_frequency",
            "last_health_check_at",
            "analytics_connected",
            "seo_baseline_ready",
            "support_until",
            "notes",
        )
        labels = {
            "title": "Название сайта",
            "user": "Клиент",
            "lead": "Заявка",
            "order": "Заказ",
            "status": "Статус",
            "support_plan": "План поддержки",
            "url": "Адрес сайта",
            "domain_name": "Домен",
            "domain_registrar": "Регистратор домена",
            "domain_expires_at": "Домен оплачен до",
            "hosting_provider": "Хостинг / VPS",
            "hosting_plan": "Тариф хостинга",
            "hosting_expires_at": "Хостинг оплачен до",
            "ssl_provider": "SSL-провайдер",
            "ssl_expires_at": "SSL действует до",
            "ssl_auto_renew": "SSL автообновляется",
            "last_backup_at": "Последний бэкап",
            "backup_frequency": "Частота бэкапов",
            "last_health_check_at": "Последняя проверка сайта",
            "analytics_connected": "Аналитика подключена",
            "seo_baseline_ready": "SEO-база готова",
            "support_until": "Поддержка оплачена до",
            "notes": "Заметки по сайту",
        }
        widgets = {
            "domain_expires_at": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "hosting_expires_at": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "ssl_expires_at": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "support_until": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "last_backup_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "last_health_check_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "notes": forms.Textarea(attrs={"rows": 5}),
        }


class ManagerMessageForm(forms.Form):
    body = forms.CharField(
        label="Ответ",
        required=False,
        max_length=4000,
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Ответ клиенту"}),
    )
    quick_reply = forms.ChoiceField(
        label="Быстрый ответ",
        required=False,
        choices=(
            ("", "Выбрать шаблон"),
            ("brief", "Запросить детали задачи"),
            ("call", "Предложить диагностику"),
            ("proposal", "Сообщить о подготовке предложения"),
            ("invoice", "Счет выставлен"),
        ),
    )

    QUICK_REPLIES = {
        "brief": "Спасибо за обращение. Чтобы точнее оценить задачу, пришлите текущий сайт/материалы, цель проекта и желаемый результат.",
        "call": "Предлагаю начать с короткой диагностики: разберем цель, текущие процессы, интеграции и приоритет запуска.",
        "proposal": "Я взял задачу в разбор. Следующим сообщением подготовлю структуру работ и предварительную вилку стоимости.",
        "invoice": "Счет подготовлен. После согласования суммы можно перейти к оплате и запуску работ.",
    }

    def clean(self):
        cleaned_data = super().clean()
        body = cleaned_data.get("body", "").strip()
        quick_reply = cleaned_data.get("quick_reply")
        if not body and quick_reply:
            cleaned_data["body"] = self.QUICK_REPLIES[quick_reply]
            return cleaned_data
        if not body:
            self.add_error("body", "Введите сообщение или выберите быстрый ответ.")
        return cleaned_data
