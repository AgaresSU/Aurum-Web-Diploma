import json
import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.core.rate_limit import rate_limited

from .models import OutboundTask, QuickConsultationBotSettings, TelegramBotSettings
from .outbox import dispatch_or_enqueue
from .quick_telegram_handlers import build_quick_telegram_webhook_response

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def telegram_webhook(request):
    secret = settings.TELEGRAM_WEBHOOK_SECRET
    try:
        config = TelegramBotSettings.objects.order_by("pk").first()
        if config and config.get_webhook_secret():
            secret = config.get_webhook_secret()
    except Exception:
        logger.exception("Could not load Telegram webhook secret from database")
    if not secret and not settings.DEBUG:
        return JsonResponse({"ok": False, "error": "Webhook secret required"}, status=503)
    if secret and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
        return JsonResponse({"ok": False, "error": "Invalid webhook secret"}, status=403)
    if rate_limited(request, "telegram-webhook", limit=60, window=60):
        return JsonResponse({"ok": False, "error": "Too many webhook requests"}, status=429)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        payload = {}

    update_id = payload.get("update_id")
    dispatch_or_enqueue(
        OutboundTask.TaskType.TELEGRAM_UPDATE,
        {"update": payload},
        f"telegram-update:{update_id}" if update_id is not None else None,
    )
    if getattr(settings, "AURUMWEB_OUTBOX_ENABLED", not settings.DEBUG):
        return JsonResponse({"ok": True, "message": "Webhook queued."})
    return JsonResponse({"ok": True, "message": "Webhook received."})


@csrf_exempt
@require_POST
def quick_telegram_webhook(request):
    secret = getattr(settings, "QUICK_TELEGRAM_WEBHOOK_SECRET", "")
    try:
        config = QuickConsultationBotSettings.objects.order_by("pk").first()
        if config and config.get_webhook_secret():
            secret = config.get_webhook_secret()
    except Exception:
        logger.exception("Could not load quick Telegram webhook secret from database")
    if not secret and not settings.DEBUG:
        return JsonResponse({"ok": False, "error": "Webhook secret required"}, status=503)
    if secret and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
        return JsonResponse({"ok": False, "error": "Invalid webhook secret"}, status=403)
    if rate_limited(request, "quick-telegram-webhook", limit=60, window=60):
        return JsonResponse({"ok": False, "error": "Too many webhook requests"}, status=429)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        payload = {}

    response = build_quick_telegram_webhook_response(payload)
    update_id = payload.get("update_id")
    deferred_telegram_method = response.get("deferred_telegram_method")
    if deferred_telegram_method:
        dispatch_or_enqueue(
            OutboundTask.TaskType.QUICK_TELEGRAM_METHOD,
            {"method": deferred_telegram_method},
            f"quick-telegram-method:{update_id}" if update_id is not None else None,
        )

    notify_consultation_id = response.get("notify_consultation_id")
    if notify_consultation_id:
        dispatch_or_enqueue(
            OutboundTask.TaskType.QUICK_CONSULTATION_CREATED,
            {"consultation_id": notify_consultation_id},
            f"quick-consultation-created:{notify_consultation_id}",
        )

    dispatch_or_enqueue(
        OutboundTask.TaskType.QUICK_WEBHOOK_EVENT,
        {"update": payload},
        f"quick-webhook-event:{update_id}" if update_id is not None else None,
    )

    telegram_method = response.get("telegram_method")
    if telegram_method:
        return JsonResponse(telegram_method)
    return JsonResponse({"ok": True, "message": "Webhook received.", "handled": response.get("handled", False)})


def health(request):
    return HttpResponse("integrations: ok", content_type="text/plain")
