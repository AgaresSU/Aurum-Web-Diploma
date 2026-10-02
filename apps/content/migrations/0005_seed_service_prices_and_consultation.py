from django.db import migrations


def seed_service_prices(apps, schema_editor):
    Service = apps.get_model("content", "Service")
    services = [
        {
            "title": "Сайты под ключ",
            "slug": "turnkey-websites",
            "service_type": "website",
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
            "service_type": "support",
            "short_description": "Контроль домена, хостинга, SSL, резервных копий, форм заявок, правок, новых страниц и технической стабильности.",
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
            "service_type": "python",
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
            "service_type": "telegram",
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
            "service_type": "consulting",
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

    for row in services:
        slug = row.pop("slug")
        Service.objects.update_or_create(slug=slug, defaults=row)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0004_service_price_amount_service_price_note_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_service_prices, noop_reverse),
    ]
