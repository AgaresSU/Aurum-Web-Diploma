import hashlib
import json
import shutil
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.billing.models import ManagedSite
from apps.integrations.models import IntegrationEvent, OutboundTask

from .models import OperationalAlert

OUTBOX_HEARTBEAT_KEY = "aurumweb:outbox-worker:heartbeat"


def record_outbox_worker_heartbeat(moment=None):
    moment = moment or timezone.now()
    stale_seconds = settings.AURUMWEB_MONITOR_WORKER_STALE_SECONDS
    cache.set(OUTBOX_HEARTBEAT_KEY, moment.isoformat(), timeout=max(stale_seconds * 4, 300))
    return moment


def outbox_worker_heartbeat_age(moment=None):
    raw_value = cache.get(OUTBOX_HEARTBEAT_KEY)
    if not raw_value:
        return None
    heartbeat = parse_datetime(str(raw_value))
    if heartbeat is None:
        return None
    if timezone.is_naive(heartbeat):
        heartbeat = timezone.make_aware(heartbeat)
    return max(0.0, ((moment or timezone.now()) - heartbeat).total_seconds())


def runtime_readiness():
    components = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        components["database"] = False
    else:
        components["database"] = True

    cache_key = "aurumweb:runtime-readiness"
    try:
        cache.set(cache_key, "ok", 30)
        components["cache"] = cache.get(cache_key) == "ok"
        cache.delete(cache_key)
    except Exception:
        components["cache"] = False

    if settings.AURUMWEB_OUTBOX_ENABLED:
        try:
            heartbeat_age = outbox_worker_heartbeat_age()
        except Exception:
            components["worker"] = False
        else:
            components["worker"] = bool(
                heartbeat_age is not None and heartbeat_age <= settings.AURUMWEB_MONITOR_WORKER_STALE_SECONDS
            )
    return {"ok": all(components.values()), "components": components}


def _condition(code, severity, title, message):
    return {"code": code, "severity": severity, "title": title, "message": message}


def _disk_conditions():
    disk_path = settings.AURUMWEB_MONITOR_DISK_PATH
    usage = shutil.disk_usage(disk_path)
    used_percent = round(usage.used / usage.total * 100, 1) if usage.total else 100.0
    if used_percent >= settings.AURUMWEB_MONITOR_DISK_CRITICAL_PERCENT:
        return [
            _condition(
                "disk-capacity",
                OperationalAlert.Severity.ERROR,
                "Заканчивается место на диске",
                f"Занято {used_percent}% файловой системы {disk_path}.",
            )
        ]
    if used_percent >= settings.AURUMWEB_MONITOR_DISK_WARNING_PERCENT:
        return [
            _condition(
                "disk-capacity",
                OperationalAlert.Severity.WARNING,
                "Мало свободного места на диске",
                f"Занято {used_percent}% файловой системы {disk_path}.",
            )
        ]
    return []


def _worker_conditions():
    if not settings.AURUMWEB_OUTBOX_ENABLED:
        return []
    heartbeat_age = outbox_worker_heartbeat_age()
    if heartbeat_age is None:
        return [
            _condition(
                "outbox-worker-heartbeat",
                OperationalAlert.Severity.ERROR,
                "Worker уведомлений не подает сигнал",
                "Не найден heartbeat процесса исходящих уведомлений.",
            )
        ]
    if heartbeat_age > settings.AURUMWEB_MONITOR_WORKER_STALE_SECONDS:
        return [
            _condition(
                "outbox-worker-heartbeat",
                OperationalAlert.Severity.ERROR,
                "Worker уведомлений остановился",
                f"Последний heartbeat был {int(heartbeat_age)} секунд назад.",
            )
        ]
    return []


def _backup_conditions(now):
    if not settings.AURUMWEB_MONITOR_BACKUPS_ENABLED:
        return []
    latest = IntegrationEvent.objects.filter(
        provider=IntegrationEvent.Provider.SYSTEM,
        title__in=("Резервное копирование завершено", "Ошибка резервного копирования"),
    ).first()
    if latest is None:
        return [
            _condition(
                "backup-freshness",
                OperationalAlert.Severity.WARNING,
                "Нет результата резервного копирования",
                "Автоматическое резервное копирование еще не зафиксировало успешный запуск.",
            )
        ]
    if latest.status == IntegrationEvent.Status.ERROR:
        return [
            _condition(
                "backup-freshness",
                OperationalAlert.Severity.ERROR,
                "Резервное копирование завершилось ошибкой",
                f"Ошибка зафиксирована {latest.created_at:%d.%m.%Y %H:%M}.",
            )
        ]
    age = now - latest.created_at
    allowed_age = timedelta(hours=settings.AURUMWEB_BACKUP_RPO_HOURS + 12)
    if age > allowed_age:
        return [
            _condition(
                "backup-freshness",
                OperationalAlert.Severity.ERROR,
                "Резервная копия устарела",
                f"Последний успешный запуск был {latest.created_at:%d.%m.%Y %H:%M}.",
            )
        ]
    return []


def _outbox_conditions(now):
    conditions = []
    failed_count = OutboundTask.objects.filter(status=OutboundTask.Status.FAILED).count()
    if failed_count:
        conditions.append(
            _condition(
                "outbox-failed-tasks",
                OperationalAlert.Severity.ERROR,
                "Есть неотправленные уведомления",
                f"Операций, исчерпавших все попытки: {failed_count}.",
            )
        )
    backlog_before = now - timedelta(minutes=settings.AURUMWEB_MONITOR_OUTBOX_BACKLOG_MINUTES)
    backlog_count = OutboundTask.objects.filter(
        status__in=(OutboundTask.Status.PENDING, OutboundTask.Status.RETRY),
        created_at__lt=backlog_before,
    ).count()
    if backlog_count:
        conditions.append(
            _condition(
                "outbox-backlog",
                OperationalAlert.Severity.WARNING,
                "Очередь уведомлений задерживается",
                f"Операций старше допустимого времени: {backlog_count}.",
            )
        )
    return conditions


def _integration_conditions(now):
    since = now - timedelta(minutes=settings.AURUMWEB_MONITOR_ERROR_WINDOW_MINUTES)
    count = IntegrationEvent.objects.filter(status=IntegrationEvent.Status.ERROR, created_at__gte=since).count()
    if not count:
        return []
    return [
        _condition(
            "recent-integration-errors",
            OperationalAlert.Severity.WARNING,
            "Свежие ошибки интеграций",
            f"За последние {settings.AURUMWEB_MONITOR_ERROR_WINDOW_MINUTES} минут зафиксировано ошибок: {count}.",
        )
    ]


def _site_expiry_conditions(today):
    conditions = []
    fields = (
        ("domain_expires_at", "domain", "Домен", "домен"),
        ("hosting_expires_at", "hosting", "Размещение сайта", "размещение сайта"),
        ("ssl_expires_at", "ssl", "Сертификат безопасности", "сертификат безопасности"),
    )
    limit = today + timedelta(days=settings.AURUMWEB_MONITOR_SITE_WARNING_DAYS)
    sites = ManagedSite.objects.filter(is_active_query(fields, limit)).only(
        "id", "title", *(field for field, _code, _title, _noun in fields)
    )
    for site in sites:
        for field, code, title, noun in fields:
            expiry = getattr(site, field)
            if expiry is None or expiry > limit:
                continue
            days_left = (expiry - today).days
            if days_left < 0:
                severity = OperationalAlert.Severity.ERROR
                message = f"У проекта «{site.title}» истек {noun}: {expiry:%d.%m.%Y}."
            else:
                severity = OperationalAlert.Severity.WARNING
                message = f"У проекта «{site.title}» {noun} оплачен до {expiry:%d.%m.%Y} ({days_left} дн.)."
            conditions.append(_condition(f"site:{site.pk}:{code}", severity, f"{title}: {site.title}", message))
    return conditions


def is_active_query(fields, limit):
    from django.db.models import Q

    query = Q()
    for field, _code, _title, _noun in fields:
        query |= Q(**{f"{field}__lte": limit})
    return query


def collect_operational_conditions(now=None):
    now = now or timezone.now()
    conditions = []
    conditions.extend(_disk_conditions())
    conditions.extend(_worker_conditions())
    conditions.extend(_backup_conditions(now))
    conditions.extend(_outbox_conditions(now))
    conditions.extend(_integration_conditions(now))
    conditions.extend(_site_expiry_conditions(timezone.localdate(now)))
    return conditions


def _details_hash(condition):
    raw = json.dumps(condition, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _send_alert_notification(alert):
    from apps.integrations.email import EmailNotificationClient
    from apps.integrations.telegram import TelegramBotClient

    message = "\n".join((alert.title, alert.message))
    email_sent = EmailNotificationClient().notify_system_alert(alert.title, message, {"alert_code": alert.code})
    telegram = TelegramBotClient()
    telegram_sent = telegram.send_message(message) if telegram.notifications_enabled else False
    return bool(email_sent or telegram_sent)


def synchronize_operational_alerts(conditions, *, notify=True, now=None):
    now = now or timezone.now()
    detected_codes = {item["code"] for item in conditions}
    notifications = []
    with transaction.atomic():
        for condition in conditions:
            details_hash = _details_hash(condition)
            alert, created = OperationalAlert.objects.select_for_update().get_or_create(
                code=condition["code"],
                defaults={
                    "severity": condition["severity"],
                    "title": condition["title"],
                    "message": condition["message"],
                    "details_hash": details_hash,
                    "first_detected_at": now,
                    "last_detected_at": now,
                },
            )
            changed = alert.details_hash != details_hash
            reopened = not alert.is_active
            if not created:
                if reopened:
                    alert.first_detected_at = now
                alert.severity = condition["severity"]
                alert.title = condition["title"]
                alert.message = condition["message"]
                alert.details_hash = details_hash
                alert.is_active = True
                alert.last_detected_at = now
                alert.resolved_at = None
                alert.save()
            repeat_due = not alert.last_notified_at or alert.last_notified_at <= now - timedelta(
                hours=settings.AURUMWEB_MONITOR_ALERT_REPEAT_HOURS
            )
            if notify and (created or changed or reopened or repeat_due):
                notifications.append(alert.pk)

        OperationalAlert.objects.filter(is_active=True).exclude(code__in=detected_codes).update(
            is_active=False,
            resolved_at=now,
            last_detected_at=now,
        )

    notified = 0
    for alert in OperationalAlert.objects.filter(pk__in=notifications):
        if _send_alert_notification(alert):
            OperationalAlert.objects.filter(pk=alert.pk).update(last_notified_at=now)
            notified += 1
    return {
        "detected": len(conditions),
        "active": OperationalAlert.objects.filter(is_active=True).count(),
        "notified": notified,
    }
