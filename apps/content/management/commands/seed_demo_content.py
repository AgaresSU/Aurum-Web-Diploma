from datetime import timedelta
from importlib import import_module

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.billing.models import ManagedSite, Order
from apps.content.models import CaseStudy, FAQItem, Service, TemplateProduct
from apps.content.presentation import client_safe_text

CLIENT_SAFE_FIELDS = {
    "title",
    "short_description",
    "full_description",
    "business_value",
    "deliverables",
    "price_note",
    "industry",
    "challenge",
    "solution",
    "result",
    "stack",
    "contents",
    "implementation_notes",
    "question",
    "answer",
}


class Command(BaseCommand):
    help = "Create demo content for AurumWeb."

    def handle(self, *args, **options):
        Service.objects.update(is_featured=False, is_published=False)
        services = [
            {
                "title": "Сайты под ключ",
                "slug": "turnkey-websites",
                "service_type": Service.ServiceType.WEBSITE,
                "short_description": "Лендинги, сайты услуг, корпоративные сайты и каталоги с формами заявок, админкой, базовым SEO и аналитикой.",
                "full_description": "Проектирую и собираю сайт под задачу клиента: структура, дизайн, страницы, формы заявок, админка, личный кабинет при необходимости и подготовка к поддержке после запуска.",
                "business_value": "Сайт ясно показывает услуги, принимает заявки и помогает быстро перейти от интереса клиента к обсуждению проекта.",
                "deliverables": "Структура сайта\nАдаптивный интерфейс\nФормы заявок\nАдминка для контента\nБазовая SEO-подготовка\nПодключение аналитики\nПодготовка к поддержке",
                "price_prefix": "от",
                "price_amount": 48000,
                "price_unit": "за проект",
                "price_note": "итоговая сумма фиксируется после разбора задачи",
                "sort_order": 10,
                "is_featured": True,
                "is_published": True,
            },
            {
                "title": "Поддержка и развитие сайтов",
                "slug": "website-care-support",
                "service_type": Service.ServiceType.SUPPORT,
                "short_description": "Контроль домена, размещения сайта, сертификата безопасности, резервных копий, форм заявок и правок.",
                "full_description": "После запуска сайт требует внимания: сроки оплат, SSL, хостинг, бэкапы, формы, аналитика, контент и безопасность. Поддержку удобно вести через личный кабинет клиента.",
                "business_value": "Поддержка держит сайт в рабочем состоянии: сроки оплат, безопасность, правки и новые задачи остаются под контролем.",
                "deliverables": "Контроль домена и хостинга\nКонтроль SSL и HTTPS\nРезервные копии\nПравки и новые блоки\nМониторинг форм заявок\nSEO-доработки\nОтчетность в кабинете клиента",
                "price_prefix": "от",
                "price_amount": 9000,
                "price_unit": "в месяц",
                "price_note": "для сайта с регулярными правками и контролем сроков",
                "sort_order": 20,
                "is_featured": True,
                "is_published": True,
            },
            {
                "title": "Python-автоматизация",
                "slug": "python-tools-automation",
                "service_type": Service.ServiceType.PYTHON,
                "short_description": "Небольшие программы и скрипты для таблиц, отчетов, парсинга, обработки файлов и связки сервисов.",
                "full_description": "Делаю практичные Python-инструменты под конкретную задачу: собрать данные, обработать файлы, сформировать отчет, убрать повторяющиеся действия или связать сервисы между собой.",
                "business_value": "Небольшие программы закрывают повторяющиеся операции: отчеты, таблицы, файлы, парсинг и обмен данными между сервисами.",
                "deliverables": "Python-скрипты и утилиты\nПарсинг и обработка данных\nExcel/CSV-автоматизация\nГенерация отчетов\nМини-панели и админки\nИнтеграции с API\nИнструкция по запуску",
                "price_prefix": "от",
                "price_amount": 15000,
                "price_unit": "за задачу",
                "price_note": "после описания входных данных и результата",
                "sort_order": 30,
                "is_featured": True,
                "is_published": True,
            },
            {
                "title": "Telegram-боты",
                "slug": "telegram-bots",
                "service_type": Service.ServiceType.TELEGRAM,
                "short_description": "Боты для заявок, уведомлений, записи, поддержки клиентов, простых кабинетов и внутренних процессов.",
                "full_description": "Создаю и поддерживаю Telegram-ботов, которые помогают быстрее принимать заявки, уведомлять менеджеров, собирать данные и дополнять сайт.",
                "business_value": "Бот принимает обращения, отправляет уведомления и помогает не пропускать важные сообщения от клиентов.",
                "deliverables": "Сценарии бота\nКоманды и кнопки\nУведомления менеджеру\nWebhook или polling\nСвязка с сайтом/CRM\nЖурнал событий\nПоддержка и доработка",
                "price_prefix": "от",
                "price_amount": 25000,
                "price_unit": "за проект",
                "price_note": "сценарии, кнопки, уведомления и запуск",
                "sort_order": 40,
                "is_featured": True,
                "is_published": True,
            },
            {
                "title": "Консультации по сайтам и автоматизации",
                "slug": "technical-consulting",
                "service_type": Service.ServiceType.CONSULTING,
                "short_description": "Разбор сайта, идеи, структуры, интеграций, оплаты, Telegram-бота или Python-задачи с понятным планом следующих шагов.",
                "full_description": "Провожу консультации по запуску и развитию сайта, подключению заявок, личного кабинета, Robokassa, Telegram-бота, SEO-базы и небольших Python-решений.",
                "business_value": "Консультация дает понятный план: что делать, в каком порядке, сколько это может стоить и где есть риски.",
                "deliverables": "Аудит текущей ситуации\nРазбор цели и сценариев клиента\nРекомендации по структуре сайта\nОценка интеграций и автоматизации\nПлан работ и следующий шаг",
                "price_prefix": "от",
                "price_amount": 5000,
                "price_unit": "за консультацию",
                "price_note": "по сайту, поддержке, боту, оплате или автоматизации",
                "sort_order": 50,
                "is_featured": True,
                "is_published": True,
            },
        ]
        services = [row.copy() for row in import_module("apps.content.migrations.0007_expand_public_services").SERVICES]

        cases = [
            {
                "title": "Единый центр заявок и счетов",
                "slug": "lead-invoice-center",
                "industry": "B2B-услуги",
                "challenge": "Заявки приходят из разных форм, теряются в переписках и не связаны со счетами.",
                "solution": "Django-админка, статусы лидов, диалоги, черновики счетов и подготовка оплаты.",
                "result": "Владелец видит путь клиента: задача, разбор, предложение, счет, оплата.",
                "stack": "Django, SQLite/PostgreSQL, Robokassa API, Telegram Bot API",
                "sort_order": 10,
                "is_featured": True,
            },
            {
                "title": "SEO-структура экспертного сайта",
                "slug": "expert-website-seo-structure",
                "industry": "Консалтинг",
                "challenge": "Сайт выглядит красиво, но не имеет посадочных страниц под спрос.",
                "solution": "Группы услуг, шаблоны описаний, FAQ, карта страниц и подготовка к публикации.",
                "result": "Сайт можно развивать и продвигать без переделки структуры.",
                "stack": "Django templates, SEO schema, sitemap",
                "sort_order": 20,
                "is_featured": True,
            },
        ]

        templates = [
            {
                "title": "Django Lead Office",
                "slug": "django-lead-office",
                "template_type": TemplateProduct.TemplateType.STARTER,
                "short_description": "Стартовая админка для заявок, диалогов, счетов и статусов.",
                "contents": "Модели заявок\nМессенджер\nСчета\nАдмин-действия\nПодготовка Robokassa",
                "implementation_notes": "Можно адаптировать под услуги, обучение, консалтинг или разработку.",
                "is_paid_ready": True,
                "sort_order": 10,
            },
            {
                "title": "Robokassa Connector",
                "slug": "robokassa-connector",
                "template_type": TemplateProduct.TemplateType.MODULE,
                "short_description": "Модуль генерации платежной ссылки и обработки ResultURL.",
                "contents": "Payment model\nПодпись платежа\nResultURL\nSuccessURL\nFailURL",
                "implementation_notes": "Перед приемом оплат нужно добавить рабочие ключи и проверить тестовый платеж.",
                "is_paid_ready": False,
                "sort_order": 20,
            },
            {
                "title": "Telegram Admin Alerts",
                "slug": "telegram-admin-alerts",
                "template_type": TemplateProduct.TemplateType.MODULE,
                "short_description": "Уведомления администратору о новых заявках и событиях интеграций.",
                "contents": "sendMessage\nWebhook для входящих событий\nЖурнал событий\nНастройки токена",
                "implementation_notes": "Следующий шаг — команды бота для просмотра новых лидов и ответа клиенту.",
                "is_paid_ready": False,
                "sort_order": 30,
            },
        ]

        faqs = [
            {
                "question": "Почему нет фиксированных цен?",
                "answer": "Стоимость зависит от задачи, объема работ, сроков и нужных функций. После короткого разбора можно назвать понятный ориентир.",
                "sort_order": 10,
            },
            {
                "question": "Можно ли подключить оплату позже?",
                "answer": "Да. Счета и платежный сценарий можно подготовить заранее, а рабочие ключи Robokassa подключить перед приемом оплат.",
                "sort_order": 20,
            },
        ]

        self._upsert(Service, services)
        self._upsert(CaseStudy, cases)
        self._upsert(TemplateProduct, templates)
        self._upsert(FAQItem, faqs, lookup="question")
        self._seed_demo_sites()
        self.stdout.write(self.style.SUCCESS("Demo content is ready."))

    def _upsert(self, model, rows, lookup="slug"):
        for row in rows:
            row = {key: client_safe_text(value) if key in CLIENT_SAFE_FIELDS else value for key, value in row.items()}
            key = row[lookup]
            model.objects.update_or_create(**{lookup: key}, defaults=row)

    def _seed_demo_sites(self):
        User = get_user_model()
        client = User.objects.filter(username="client_demo").first()
        if not client:
            return
        order = Order.objects.filter(user=client).first()
        today = timezone.localdate()
        ManagedSite.objects.update_or_create(
            user=client,
            title="Сайт юридического эксперта",
            defaults={
                "order": order,
                "url": "https://legal-demo.example",
                "domain_name": "legal-demo.example",
                "status": ManagedSite.Status.MAINTENANCE,
                "support_plan": ManagedSite.SupportPlan.GROWTH,
                "domain_registrar": "Reg.ru",
                "domain_expires_at": today + timedelta(days=178),
                "hosting_provider": "Timeweb Cloud",
                "hosting_plan": "VPS Start",
                "hosting_expires_at": today + timedelta(days=46),
                "ssl_provider": "Let's Encrypt",
                "ssl_expires_at": today + timedelta(days=64),
                "ssl_auto_renew": True,
                "last_backup_at": timezone.now() - timedelta(days=2),
                "backup_frequency": "Еженедельно",
                "last_health_check_at": timezone.now() - timedelta(hours=6),
                "analytics_connected": True,
                "seo_baseline_ready": True,
                "support_until": today + timedelta(days=31),
                "notes": "Демо-карточка для кабинета клиента: контроль домена, сертификата безопасности, хостинга, резервных копий и поддержки.",
            },
        )
