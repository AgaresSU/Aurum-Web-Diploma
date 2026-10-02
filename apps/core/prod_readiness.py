import os
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, connection
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.crypto import get_random_string

from apps.accounts.models import Profile
from apps.content.models import Service, TemplateProduct
from apps.core.models import OperationalAlert
from apps.core.monitoring import outbox_worker_heartbeat_age
from apps.core.views import LEGAL_PAGES
from apps.integrations.models import IntegrationEvent
from apps.integrations.robokassa import RobokassaClient
from apps.integrations.telegram import TelegramBotClient

READINESS_STATES = {"ok", "warning", "error"}
PLACEHOLDER_MARKERS = ("example", "localhost", "127.0.0.1", "replace-with", "smtp.example.com")


def readiness_item(title, state, text, action="", url=""):
    return {
        "title": title,
        "state": state,
        "text": text,
        "action": action,
        "url": url,
    }


def flatten_readiness(groups):
    return [item for group in groups for item in group["items"]]


def readiness_score(items):
    scored = [item for item in items if item["state"] in READINESS_STATES]
    if not scored:
        return 0
    ok_count = sum(1 for item in scored if item["state"] == "ok")
    return round(ok_count / len(scored) * 100)


def _has_placeholder(value):
    normalized = str(value or "").strip().lower()
    return any(marker in normalized for marker in PLACEHOLDER_MARKERS)


def readiness_summary(groups):
    all_items = flatten_readiness(groups)
    blockers = [item for item in all_items if item["state"] == "error"]
    warnings = [item for item in all_items if item["state"] == "warning"]
    return {
        "score": readiness_score(all_items),
        "ok": sum(1 for item in all_items if item["state"] == "ok"),
        "warnings": len(warnings),
        "blockers": len(blockers),
        "total": len(all_items),
        "warning_items": warnings,
        "blocker_items": blockers,
    }


def _site_url_state():
    site_url = settings.AURUMWEB_SITE_URL.strip().rstrip("/")
    if not site_url:
        return readiness_item(
            "Адрес сайта",
            "warning",
            "Адрес сайта пока не задан. Для публикации нужен основной HTTPS-домен.",
        )
    if not site_url.startswith("https://"):
        return readiness_item(
            "Адрес сайта",
            "error",
            "Адрес сайта должен открываться по HTTPS, иначе уведомления и возвраты после оплаты будут ненадежны.",
        )
    if _has_placeholder(site_url):
        return readiness_item(
            "Адрес сайта",
            "warning",
            "Сейчас указан временный адрес. После выбора домена замените его на реальный HTTPS-адрес сайта.",
        )
    return readiness_item("Адрес сайта", "ok", site_url)


def _debug_state():
    return readiness_item(
        "Режим сайта",
        "warning" if settings.DEBUG else "ok",
        (
            "Сейчас включен локальный режим. Перед публикацией нужно включить рабочий режим сайта."
            if settings.DEBUG
            else "Рабочий режим сайта включен."
        ),
    )


def _production_database_state():
    database_engine = connection.settings_dict.get("ENGINE", "")
    return readiness_item(
        "База данных",
        "ok" if "postgresql" in database_engine else "warning",
        (
            "Подключена серверная база данных."
            if "postgresql" in database_engine
            else "Сейчас используется локальная база. Для сервера лучше подключить отдельную серверную базу данных."
        ),
    )


def _database_runtime_state():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:
        return readiness_item("Подключение базы", "error", f"База данных не отвечает: {exc.__class__.__name__}.")
    return readiness_item("Подключение базы", "ok", "База данных отвечает.")


def _cache_runtime_state():
    key = f"prod-readiness:{get_random_string(12)}"
    try:
        cache.set(key, "ok", 30)
        if cache.get(key) != "ok":
            return readiness_item(
                "Кэш работает", "warning", "Кэш отвечает, но тестовое значение не прочиталось обратно."
            )
    except Exception as exc:
        return readiness_item("Кэш работает", "error", f"Кэш не отвечает: {exc.__class__.__name__}.")
    return readiness_item("Кэш работает", "ok", "Тестовая запись в кэш прошла успешно.")


def _totp_state(admin_user=None):
    try:
        if admin_user is not None and getattr(admin_user, "is_authenticated", False):
            profile = getattr(admin_user, "profile", None)
            enabled = bool(profile and profile.totp_enabled and profile.get_totp_secret())
        else:
            enabled = any(
                profile.get_totp_secret()
                for profile in Profile.objects.filter(user__is_staff=True, role=Profile.Role.ADMIN, totp_enabled=True)
            )
    except DatabaseError as exc:
        return readiness_item(
            "Второй код входа",
            "warning",
            f"Не удалось проверить второй код входа: {exc.__class__.__name__}.",
            "Безопасность",
            "/office/security/",
        )

    return readiness_item(
        "Второй код входа",
        "ok" if enabled else "error",
        ("Двухфакторная защита включена." if enabled else "Нужно включить второй код для входа в кабинет менеджера."),
        "Безопасность",
        "/office/security/",
    )


def _platform_readiness(admin_user=None):
    secret_key_ready = bool(settings.SECRET_KEY and settings.SECRET_KEY != "dev-only-change-me")
    local_hosts = {"localhost", "127.0.0.1"}
    public_hosts = [host for host in settings.ALLOWED_HOSTS if host not in local_hosts]
    has_placeholder_hosts = any(_has_placeholder(host) for host in public_hosts)
    return [
        _debug_state(),
        readiness_item(
            "Секретный ключ",
            "ok" if secret_key_ready else "error",
            "Секретный ключ задан." if secret_key_ready else "Нужно заменить временный секретный ключ на рабочий.",
        ),
        readiness_item(
            "Разрешенные хосты",
            "warning" if has_placeholder_hosts else "ok" if public_hosts or settings.DEBUG else "error",
            f"Разрешенные адреса: {', '.join(settings.ALLOWED_HOSTS) or 'не заданы'}",
        ),
        _production_database_state(),
        _database_runtime_state(),
        _cache_runtime_state(),
        _totp_state(admin_user),
        readiness_item(
            "Защита сессий",
            "ok" if settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE else "warning",
            (
                "Куки передаются только по защищенному соединению."
                if settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE
                else "В локальном режиме это допустимо. На сервере куки должны передаваться только по HTTPS."
            ),
        ),
    ]


def _telegram_readiness():
    client = TelegramBotClient()
    config = client.config
    webhook_url = ((config.webhook_url if config else "") or settings.TELEGRAM_WEBHOOK_URL or "").strip()
    has_site_url = bool(settings.AURUMWEB_SITE_URL.strip())
    expected_webhook_ready = bool(
        (webhook_url or has_site_url)
        and ((config.get_webhook_secret() if config else "") or settings.TELEGRAM_WEBHOOK_SECRET)
    )
    webhook_is_placeholder = _has_placeholder(webhook_url or settings.AURUMWEB_SITE_URL)
    return [
        readiness_item(
            "Уведомления Telegram",
            "ok" if client.configured else "error",
            (
                "Telegram-бот настроен и может отправлять уведомления."
                if client.configured
                else "Нужно сохранить ключ бота, выбрать чат для уведомлений и включить отправку."
            ),
            "Открыть интеграции",
            "/office/integrations/",
        ),
        readiness_item(
            "Имя бота",
            "ok" if client.bot_username else "warning",
            f"@{client.bot_username}" if client.bot_username else "Имя появится после проверки бота.",
            "Проверить бота",
            "/office/integrations/",
        ),
        readiness_item(
            "Мгновенные уведомления",
            "warning" if webhook_is_placeholder else "ok" if expected_webhook_ready else "warning",
            (
                "Домен и ключ уведомлений заданы."
                if expected_webhook_ready and not webhook_is_placeholder
                else (
                    "Сейчас указан временный адрес. После выбора домена нужно обновить подключение уведомлений."
                    if webhook_is_placeholder
                    else "После подключения домена нужно включить мгновенные уведомления Telegram."
                )
            ),
            "Настроить уведомления",
            "/office/integrations/",
        ),
    ]


def _payment_readiness():
    client = RobokassaClient()
    items = []
    if not client.configured:
        items.append(
            readiness_item(
                "Robokassa",
                "warning",
                "Пока прием оплат не готов полностью. Перед оплатами нужно сохранить ключи магазина.",
                "Открыть интеграции",
                "/office/integrations/",
            )
        )
    elif client.config.test_mode:
        items.append(readiness_item("Robokassa", "warning", "Ключи сохранены, но включен тестовый режим."))
    else:
        items.append(readiness_item("Robokassa", "ok", "Прием оплат включен."))
    items.append(
        readiness_item(
            "Подпись Robokassa",
            "error" if client.config.hash_algorithm.lower() == "md5" else "ok",
            (
                "Сейчас используется MD5. Переход на SHA-256 или SHA-512 выполняется одновременно "
                "в кабинете Robokassa и настройках сайта."
                if client.config.hash_algorithm.lower() == "md5"
                else f"Используется {client.config.hash_algorithm.upper()}."
            ),
            "Открыть интеграции",
            "/office/integrations/",
        )
    )
    items.append(
        readiness_item(
            "Чеки для оплаты",
            "ok" if client.config.receipt_enabled else "error",
            (
                "Чеки включены и будут отправляться с позициями счета."
                if client.config.receipt_enabled
                else "Чеки выключены. Для оплаты услуг самозанятого нужно передавать позиции счета."
            ),
            "Открыть интеграции",
            "/office/integrations/",
        )
    )

    seller_missing = []
    if not settings.AURUMWEB_SELLER_NAME.strip():
        seller_missing.append("ФИО продавца")
    if not settings.AURUMWEB_SELLER_INN.strip():
        seller_missing.append("ИНН")
    if not settings.AURUMWEB_PUBLIC_EMAIL.strip():
        seller_missing.append("email")
    if not settings.AURUMWEB_PUBLIC_PHONE.strip():
        seller_missing.append("телефон")
    items.append(
        readiness_item(
            "Реквизиты продавца",
            "error" if seller_missing else "ok",
            (
                "Не заполнено: "
                + ", ".join(seller_missing)
                + ". Для подключения оплат эти данные должны быть видны на сайте."
                if seller_missing
                else "ФИО, статус, ИНН, email и телефон продавца заполнены."
            ),
            "Открыть контакты",
            "/legal/contacts/",
        )
    )

    try:
        priced_services = Service.objects.filter(is_published=True, price_amount__isnull=False).count()
    except DatabaseError as exc:
        priced_services = None
        services_error = exc
    else:
        services_error = None
    items.append(
        readiness_item(
            "Услуги и цены",
            "warning" if priced_services is None else "ok" if priced_services >= 5 else "warning",
            (
                f"Не удалось проверить услуги до применения миграций: {services_error.__class__.__name__}."
                if priced_services is None
                else f"Опубликовано услуг с ценой: {priced_services}. Для модерации должны быть понятные услуги и стоимость."
            ),
            "Услуги и цены",
            "/office/services/",
        )
    )
    items.append(
        readiness_item(
            "Условия на сайте",
            "ok",
            "На сайте есть страницы оплаты, возврата, оферты, политики данных и контактов.",
            "Открыть оплату",
            "/legal/payment/",
        )
    )
    return items


def _email_readiness():
    enabled = settings.AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED or settings.AURUMWEB_CLIENT_EMAIL_NOTIFICATIONS_ENABLED
    backend = settings.EMAIL_BACKEND.rsplit(".", 1)[-1]
    smtp_is_placeholder = _has_placeholder(settings.EMAIL_HOST) or _has_placeholder(settings.EMAIL_HOST_PASSWORD)
    if not enabled:
        return [
            readiness_item(
                "Email-уведомления",
                "warning",
                "Email-уведомления выключены. Telegram уже помогает, но для сервера лучше иметь резервный канал.",
            )
        ]
    if "console" in settings.EMAIL_BACKEND or "locmem" in settings.EMAIL_BACKEND:
        return [
            readiness_item(
                "Email-уведомления", "warning", f"Включены, но сейчас используется {backend}. Для сервера нужен SMTP."
            )
        ]
    if smtp_is_placeholder:
        return [
            readiness_item("Email-уведомления", "warning", "SMTP включен, но в настройках еще стоят тестовые значения.")
        ]
    return [readiness_item("Email-уведомления", "ok", f"Включены через {backend}.")]


def _legal_readiness():
    required_slugs = ("payment", "refund", "privacy", "offer", "contacts")
    missing_pages = [slug for slug in required_slugs if slug not in LEGAL_PAGES]
    broken_routes = []
    for slug in required_slugs:
        try:
            reverse("legal-page", kwargs={"slug": slug})
        except NoReverseMatch:
            broken_routes.append(slug)

    seller_missing = [
        title
        for title, value in (
            ("ФИО продавца", settings.AURUMWEB_SELLER_NAME),
            ("ИНН", settings.AURUMWEB_SELLER_INN),
            ("email", settings.AURUMWEB_PUBLIC_EMAIL),
            ("телефон", settings.AURUMWEB_PUBLIC_PHONE),
        )
        if not str(value or "").strip()
    ]

    pages_state = "error" if broken_routes else "warning" if missing_pages else "ok"
    return [
        readiness_item(
            "Юридические страницы",
            pages_state,
            (
                "Оплата, возврат, политика данных, оферта и контакты доступны."
                if pages_state == "ok"
                else "Нужно проверить страницы: " + ", ".join(missing_pages or broken_routes)
            ),
            "Открыть оферту",
            "/legal/offer/",
        ),
        readiness_item(
            "Реквизиты продавца",
            "error" if seller_missing else "ok",
            (
                "ФИО, статус, ИНН, email и телефон заполнены."
                if not seller_missing
                else "Не заполнено: " + ", ".join(seller_missing)
            ),
            "Открыть контакты",
            "/legal/contacts/",
        ),
    ]


def _backup_readiness():
    backup_dir = Path(
        os.getenv("AURUMWEB_BACKUP_DIR", str(getattr(settings, "AURUMWEB_BACKUP_DIR", settings.BASE_DIR / "backups")))
    )
    existing_parent = backup_dir
    while not existing_parent.exists() and existing_parent != existing_parent.parent:
        existing_parent = existing_parent.parent
    parent_ready = backup_dir.exists() or existing_parent.is_dir()
    backup_files = []
    if backup_dir.exists():
        backup_files = [path for path in backup_dir.rglob("*") if path.is_file()]
    latest_backup = max(backup_files, key=lambda path: path.stat().st_mtime, default=None)
    latest_backup_age = None
    if latest_backup:
        latest_backup_age = (
            datetime.now().astimezone() - datetime.fromtimestamp(latest_backup.stat().st_mtime).astimezone()
        )
    s3_uri = getattr(settings, "AURUMWEB_BACKUP_S3_URI", "")
    rclone_remote = getattr(settings, "AURUMWEB_BACKUP_RCLONE_REMOTE", "")
    rclone_path = getattr(settings, "AURUMWEB_BACKUP_RCLONE_PATH", "")
    age_recipient = getattr(settings, "AURUMWEB_BACKUP_AGE_RECIPIENT", "")
    external_destination_ready = bool(s3_uri or (rclone_remote and rclone_path))
    rpo_hours = getattr(settings, "AURUMWEB_BACKUP_RPO_HOURS", 24)
    latest_backup_event = IntegrationEvent.objects.filter(
        provider=IntegrationEvent.Provider.SYSTEM,
        title__in=("Резервное копирование завершено", "Ошибка резервного копирования"),
    ).first()
    latest_restore_event = IntegrationEvent.objects.filter(
        provider=IntegrationEvent.Provider.SYSTEM,
        title__in=("Тестовое восстановление завершено", "Ошибка тестового восстановления"),
    ).first()
    restore_is_fresh = bool(
        latest_restore_event
        and latest_restore_event.status == IntegrationEvent.Status.SUCCESS
        and latest_restore_event.created_at >= timezone.now() - timedelta(days=35)
    )
    return [
        readiness_item(
            "Папка бэкапов",
            "ok" if backup_dir.exists() else "warning" if parent_ready else "error",
            (
                f"Папка есть: {backup_dir}"
                if backup_dir.exists()
                else (
                    f"Папка будет создана при первом резервном копировании: {backup_dir}"
                    if parent_ready
                    else f"Папка недоступна: {backup_dir.parent}"
                )
            ),
        ),
        readiness_item(
            "Резервное копирование",
            (
                "warning"
                if latest_backup_event is None
                else "error" if latest_backup_event.status == IntegrationEvent.Status.ERROR else "ok"
            ),
            (
                "Результат автоматического запуска еще не записан."
                if latest_backup_event is None
                else f"Последний запуск: {latest_backup_event.created_at:%d.%m.%Y %H:%M}."
            ),
        ),
        readiness_item(
            "Свежий бэкап",
            (
                "warning"
                if latest_backup is None
                else (
                    "ok"
                    if latest_backup_age is not None and latest_backup_age <= timedelta(hours=rpo_hours + 12)
                    else "warning"
                )
            ),
            (
                "В папке бэкапов пока нет файлов."
                if latest_backup is None
                else f"Последний файл: {latest_backup.relative_to(backup_dir)}."
            ),
        ),
        readiness_item(
            "Внешняя зашифрованная копия",
            "ok" if external_destination_ready and age_recipient else "warning",
            (
                "Внешнее хранилище и получатель age настроены."
                if external_destination_ready and age_recipient
                else "Нужно задать внешнее хранилище и открытый ключ age."
            ),
        ),
        readiness_item(
            "Тестовое восстановление",
            "ok" if restore_is_fresh else "warning",
            (
                f"Последняя успешная проверка: {latest_restore_event.created_at:%d.%m.%Y %H:%M}."
                if restore_is_fresh
                else "Нужна успешная проверка восстановления не старше 35 дней."
            ),
        ),
    ]


def _logging_readiness():
    active_handlers = getattr(settings, "ACTIVE_LOG_HANDLERS", ["console"])
    log_file = getattr(settings, "LOG_FILE", "")
    request_level = getattr(settings, "REQUEST_LOG_LEVEL", "WARNING")
    return [
        readiness_item(
            "Журнал событий",
            "ok" if active_handlers else "error",
            f"Журнал настроен: {', '.join(active_handlers) or 'не задано'}.",
        ),
        readiness_item(
            "Файловые логи",
            "ok" if log_file else "warning",
            (
                f"Журнал пишется в файл: {log_file}"
                if log_file
                else "Сейчас журнал выводится только в консоль. На сервере лучше включить запись в файл."
            ),
        ),
        readiness_item(
            "Ошибки сайта",
            "ok",
            f"Ошибки сайта фиксируются на уровне {request_level} и выше.",
        ),
    ]


def _monitoring_readiness():
    try:
        active_errors = OperationalAlert.objects.filter(
            is_active=True,
            severity=OperationalAlert.Severity.ERROR,
        ).count()
        active_warnings = OperationalAlert.objects.filter(
            is_active=True,
            severity=OperationalAlert.Severity.WARNING,
        ).count()
    except DatabaseError as exc:
        alert_item = readiness_item(
            "Состояние системы",
            "warning",
            f"Не удалось прочитать предупреждения: {exc.__class__.__name__}.",
        )
    else:
        alert_state = "error" if active_errors else "warning" if active_warnings else "ok"
        alert_item = readiness_item(
            "Состояние системы",
            alert_state,
            (
                f"Критических ошибок: {active_errors}, предупреждений: {active_warnings}."
                if active_errors or active_warnings
                else "Активных эксплуатационных предупреждений нет."
            ),
        )

    if not settings.AURUMWEB_OUTBOX_ENABLED:
        worker_item = readiness_item(
            "Worker уведомлений",
            "warning",
            "Очередь уведомлений выключена. Это допустимо только при локальной разработке.",
        )
    else:
        try:
            heartbeat_age = outbox_worker_heartbeat_age()
        except Exception as exc:
            worker_item = readiness_item(
                "Worker уведомлений",
                "error",
                f"Не удалось проверить worker: {exc.__class__.__name__}.",
            )
        else:
            worker_ready = bool(
                heartbeat_age is not None and heartbeat_age <= settings.AURUMWEB_MONITOR_WORKER_STALE_SECONDS
            )
            worker_item = readiness_item(
                "Worker уведомлений",
                "ok" if worker_ready else "error",
                (
                    f"Последний сигнал получен {int(heartbeat_age)} секунд назад."
                    if heartbeat_age is not None
                    else "Сигнал от worker еще не получен."
                ),
            )
    return [alert_item, worker_item]


def _content_readiness():
    try:
        templates_count = TemplateProduct.objects.filter(
            is_published=True,
            template_type=TemplateProduct.TemplateType.WEBSITE,
        ).count()
    except DatabaseError as exc:
        templates_count = None
        templates_error = exc
    else:
        templates_error = None

    try:
        recent_error_since = timezone.now() - timedelta(hours=2)
        recent_errors = IntegrationEvent.objects.filter(
            status=IntegrationEvent.Status.ERROR,
            created_at__gte=recent_error_since,
        ).count()
    except DatabaseError as exc:
        recent_errors = None
        integration_error = exc
    else:
        integration_error = None

    return [
        readiness_item(
            "Шаблоны сайтов",
            "warning" if templates_count is None else "ok" if templates_count >= 25 else "warning",
            (
                f"Не удалось проверить шаблоны до применения миграций: {templates_error.__class__.__name__}."
                if templates_count is None
                else f"Опубликовано демо-шаблонов: {templates_count}."
            ),
            "Открыть шаблоны",
            "/content/templates/",
        ),
        readiness_item(
            "Ошибки интеграций",
            "warning" if recent_errors is None else "ok" if recent_errors == 0 else "warning",
            (
                f"Не удалось проверить журнал до применения миграций: {integration_error.__class__.__name__}."
                if recent_errors is None
                else (
                    "Свежих ошибок интеграций нет."
                    if recent_errors == 0
                    else f"В журнале есть свежие ошибки интеграций за последние 2 часа: {recent_errors}."
                )
            ),
            "Открыть интеграции",
            "/office/integrations/",
        ),
    ]


def _launch_panel_readiness():
    email_ready = _email_readiness()[0]
    telegram_ready = _telegram_readiness()[0]
    payment_ready = _payment_readiness()[0]
    legal_ready = _legal_readiness()[0]
    backup_ready = _backup_readiness()[0]
    logging_ready = _logging_readiness()[0]
    monitoring_ready = _monitoring_readiness()[0]
    return [
        _site_url_state(),
        _debug_state(),
        _production_database_state(),
        email_ready,
        telegram_ready,
        payment_ready,
        legal_ready,
        backup_ready,
        logging_ready,
        monitoring_ready,
    ]


def build_readiness(admin_user=None):
    return [
        {"title": "Панель запуска", "items": _launch_panel_readiness()},
        {"title": "Платформа", "items": _platform_readiness(admin_user)},
        {"title": "Telegram", "items": _telegram_readiness()},
        {"title": "Оплата", "items": _payment_readiness()},
        {"title": "Email", "items": _email_readiness()},
        {"title": "Юридический блок", "items": _legal_readiness()},
        {
            "title": "Эксплуатационный контроль",
            "items": _monitoring_readiness() + _backup_readiness() + _logging_readiness(),
        },
        {"title": "Контент и журнал", "items": _content_readiness()},
    ]
