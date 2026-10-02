import hashlib
import logging
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.utils import timezone


class SafeErrorEmailHandler(logging.Handler):
    """Send a throttled error summary without request data or stack contents."""

    def emit(self, record):
        try:
            recipients = tuple(getattr(settings, "AURUMWEB_ADMIN_EMAILS", ()))
            if not recipients:
                return
            request = getattr(record, "request", None)
            path = getattr(request, "path", "") if request is not None else ""
            exception_name = record.exc_info[0].__name__ if record.exc_info else "RuntimeError"
            fingerprint = "|".join((record.name, exception_name, path, str(record.lineno)))
            digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:20]
            if not cache.add(f"aurumweb:error-email:{digest}", "1", timeout=900):
                return
            body = "\n".join(
                (
                    "AurumWeb зафиксировал ошибку приложения.",
                    f"Время: {timezone.now():%d.%m.%Y %H:%M:%S %Z}",
                    f"Компонент: {record.name}",
                    f"Исключение: {exception_name}",
                    f"Маршрут: {path or '-'}",
                    f"Файл: {Path(record.pathname).name}:{record.lineno}",
                    "Подробности доступны в системном журнале сервера.",
                )
            )
            send_mail(
                f"{settings.AURUMWEB_EMAIL_SUBJECT_PREFIX}Ошибка приложения",
                body,
                settings.SERVER_EMAIL,
                recipients,
                fail_silently=True,
            )
        except Exception:
            self.handleError(record)
