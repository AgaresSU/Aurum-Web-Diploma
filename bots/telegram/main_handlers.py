from datetime import timedelta
from urllib.parse import urlencode

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Profile
from apps.accounts.telegram_registration import make_telegram_registration_token
from apps.billing.models import Invoice, ManagedSite, Order
from apps.integrations.models import IntegrationEvent, TelegramBotSettings
from apps.leads.models import Lead
from apps.messaging.models import Conversation, Message

from .clients import TelegramBotClient

TELEGRAM_PENDING_LINK_TITLE = "Telegram: ожидание привязки клиента"


def _clip(value, limit=260):
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


def _absolute_url(path):
    return TelegramBotClient()._absolute_url(path)


def _telegram_registration_url(chat_id, message):
    from_user = message.get("from") or {}
    token = make_telegram_registration_token(
        chat_id=chat_id,
        username=from_user.get("username", ""),
        first_name=from_user.get("first_name", ""),
        last_name=from_user.get("last_name", ""),
    )
    return _absolute_url(f"{reverse('accounts:register')}?{urlencode({'telegram': token})}")


def _telegram_entry_keyboard(chat_id, message):
    return {
        "inline_keyboard": [
            [{"text": "Зарегистрироваться", "url": _telegram_registration_url(chat_id, message)}],
            [
                {
                    "text": "Войти в кабинет",
                    "url": _absolute_url(f"{reverse('accounts:login')}?fresh=1"),
                }
            ],
        ]
    }


def _extract_message(update):
    for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
        message = update.get(key)
        if message:
            return message
    callback = update.get("callback_query", {})
    if callback.get("message"):
        return callback["message"]
    return None


def _extract_callback(update):
    return update.get("callback_query") or {}


def _callback_command(data):
    return {
        "admin:status": "/status",
        "admin:leads": "/leads",
        "admin:dialogs": "/dialogs",
        "admin:today": "/today",
        "admin:help": "/help",
        "client:status": "/status",
        "client:stop": "/stop",
    }.get(str(data or "").strip(), "")


def _callback_data(callback):
    return str((callback or {}).get("data") or "").strip()


def _chat_id(message):
    chat = message.get("chat") or {}
    value = chat.get("id")
    return str(value) if value is not None else ""


def _command(text):
    first = str(text or "").strip().split(maxsplit=1)[0].lower()
    return first.split("@", 1)[0]


def _command_parts(text):
    parts = str(text or "").strip().split(maxsplit=1)
    command = parts[0].lower().split("@", 1)[0] if parts else ""
    payload = parts[1].strip() if len(parts) > 1 else ""
    return command, payload


def _normalise_link_code(value):
    return "".join(str(value or "").strip().upper().split())


def _help_text():
    return "\n".join(
        [
            "AurumWeb Office",
            "",
            "/status — сводка по проектам",
            "/leads — последние заявки",
            "/dialogs — диалоги, где клиент ждет ответа",
            "/today — счета, сроки и сайты под вниманием",
            "/help — список команд",
        ]
    )


def _status_text():
    open_orders = Order.objects.exclude(status__in=(Order.Status.COMPLETED, Order.Status.CANCELLED)).count()
    today = timezone.localdate()
    attention_sites = ManagedSite.objects.filter(
        Q(status=ManagedSite.Status.ACTION_REQUIRED)
        | Q(domain_expires_at__lte=today + timedelta(days=30))
        | Q(hosting_expires_at__lte=today + timedelta(days=30))
        | Q(ssl_expires_at__lte=today + timedelta(days=30))
    ).count()
    return "\n".join(
        [
            "Сводка AurumWeb Office",
            "",
            f"Новые заявки: {Lead.objects.filter(status=Lead.Status.NEW).count()}",
            f"Диалоги ждут ответа: {Conversation.objects.filter(status=Conversation.Status.WAITING_MANAGER).count()}",
            f"Счета-черновики: {Invoice.objects.filter(status=Invoice.Status.DRAFT).count()}",
            f"Выставлены, но не оплачены: {Invoice.objects.filter(status=Invoice.Status.ISSUED).count()}",
            f"Активные заказы: {open_orders}",
            f"Сайты под вниманием: {attention_sites}",
            "",
            f"Office: {_absolute_url(reverse('office:dashboard'))}",
        ]
    )


def _recent_leads_text():
    leads = Lead.objects.order_by("-created_at")[:5]
    if not leads:
        return "Заявок пока нет."
    lines = ["Последние заявки", ""]
    for lead in leads:
        lines.extend(
            [
                f"#{lead.pk} · {lead.get_status_display()}",
                f"{lead.name or 'Без имени'} · {lead.email or '-'}",
                _clip(lead.subject or lead.service_type or lead.task, 180),
                _absolute_url(reverse("office:lead-detail", kwargs={"pk": lead.pk})),
                "",
            ]
        )
    return "\n".join(lines).strip()


def _dialogs_text():
    conversations = Conversation.objects.filter(status=Conversation.Status.WAITING_MANAGER).order_by("-updated_at")[:5]
    if not conversations:
        return "Диалогов, где клиент ждет ответа, сейчас нет."
    lines = ["Диалоги ждут ответа", ""]
    for conversation in conversations:
        last_message = (
            conversation.messages.filter(author_role=Message.AuthorRole.CLIENT, is_internal=False)
            .order_by("-created_at")
            .first()
        )
        lines.extend(
            [
                f"#{conversation.pk} · {conversation.title}",
                f"Email: {conversation.client_email or '-'}",
                _clip(last_message.body if last_message else "", 180),
                _absolute_url(reverse("office:conversation-detail", kwargs={"pk": conversation.pk})),
                "",
            ]
        )
    return "\n".join(lines).strip()


def _today_text():
    today = timezone.localdate()
    soon = today + timedelta(days=30)
    invoices = Invoice.objects.filter(status=Invoice.Status.ISSUED).order_by("due_date", "-created_at")[:5]
    orders = (
        Order.objects.filter(due_at__lte=soon)
        .exclude(status__in=(Order.Status.COMPLETED, Order.Status.CANCELLED))
        .order_by("due_at")[:5]
    )
    sites = ManagedSite.objects.filter(
        Q(status=ManagedSite.Status.ACTION_REQUIRED)
        | Q(domain_expires_at__lte=soon)
        | Q(hosting_expires_at__lte=soon)
        | Q(ssl_expires_at__lte=soon)
    ).order_by("domain_expires_at", "hosting_expires_at", "ssl_expires_at")[:5]

    lines = ["Что требует внимания", ""]
    if invoices:
        lines.append("Счета к оплате:")
        for invoice in invoices:
            lines.append(f"#{invoice.pk} · {invoice.amount} ₽ · {invoice.client_name or '-'}")
    if orders:
        lines.extend(["", "Сроки заказов:"])
        for order in orders:
            lines.append(f"#{order.pk} · {order.due_at or '-'} · {order.title}")
    if sites:
        lines.extend(["", "Сайты:"])
        for site in sites:
            date_bits = [
                f"домен {site.domain_expires_at}" if site.domain_expires_at and site.domain_expires_at <= soon else "",
                (
                    f"хостинг {site.hosting_expires_at}"
                    if site.hosting_expires_at and site.hosting_expires_at <= soon
                    else ""
                ),
                f"SSL {site.ssl_expires_at}" if site.ssl_expires_at and site.ssl_expires_at <= soon else "",
            ]
            lines.append(f"{site.title} · {', '.join(bit for bit in date_bits if bit) or site.get_status_display()}")
    if len(lines) == 2:
        lines.append("На ближайшие 30 дней критичных пунктов нет.")
    lines.extend(["", f"Office: {_absolute_url(reverse('office:dashboard'))}"])
    return "\n".join(lines)


def _admin_response(command):
    if command in ("/start", "/help"):
        return _help_text()
    if command == "/status":
        return _status_text()
    if command == "/leads":
        return _recent_leads_text()
    if command == "/dialogs":
        return _dialogs_text()
    if command == "/today":
        return _today_text()
    return "Команда не распознана.\n\n" + _help_text()


def _bound_client_profile(chat_id):
    return Profile.objects.select_related("user").filter(telegram_chat_id=str(chat_id)).first()


def _link_client_profile(chat_id, code, message):
    normalised_code = _normalise_link_code(code)
    client = TelegramBotClient()
    if not normalised_code:
        client.send_message(
            "Это бот AurumWeb. Если кабинета еще нет, зарегистрируйтесь по кнопке ниже — Telegram "
            "подключится автоматически. Если кабинет уже есть, войдите в него и подключите Telegram "
            "в разделе “Реквизиты”.",
            chat_id=chat_id,
            reply_markup=_telegram_entry_keyboard(chat_id, message),
        )
        return {"ok": True, "handled": True, "client": True, "linked": False, "reason": "code_missing"}

    profile = Profile.objects.select_related("user").filter(telegram_link_code__iexact=normalised_code).first()
    if not profile:
        client.send_message(
            "Код привязки не найден. Проверьте код в личном кабинете или обновите его в разделе “Реквизиты”.",
            chat_id=chat_id,
        )
        IntegrationEvent.objects.create(
            provider=IntegrationEvent.Provider.TELEGRAM,
            status=IntegrationEvent.Status.WARNING,
            title="Telegram: неверный код привязки клиента",
            payload={"chat_id": chat_id, "code": normalised_code},
        )
        return {"ok": True, "handled": True, "client": True, "linked": False, "reason": "code_invalid"}

    if profile.telegram_link_code_created_at and profile.telegram_link_code_created_at < timezone.now() - timedelta(
        days=30
    ):
        client.send_message(
            "Код привязки устарел. Обновите код в личном кабинете AurumWeb и отправьте новую команду.",
            chat_id=chat_id,
        )
        return {"ok": True, "handled": True, "client": True, "linked": False, "reason": "code_expired"}

    from_user = message.get("from") or {}
    username = from_user.get("username")
    update_fields = [
        "telegram_chat_id",
        "telegram_notifications_enabled",
        "telegram_link_code",
        "telegram_link_code_created_at",
    ]
    profile.telegram_chat_id = str(chat_id)
    profile.telegram_notifications_enabled = True
    profile.telegram_link_code = ""
    profile.telegram_link_code_created_at = None
    if username and not profile.telegram_username:
        profile.telegram_username = f"@{username}"
        update_fields.append("telegram_username")
    profile.save(update_fields=update_fields)

    client.send_message(
        "Telegram подключен к личному кабинету AurumWeb. Теперь сюда будут приходить уведомления по заявкам, "
        "диалогам, счетам, оплатам, заказам и сайту. Отключить уведомления можно в личном кабинете.",
        chat_id=chat_id,
        reply_markup=client.client_keyboard(),
    )
    IntegrationEvent.objects.create(
        provider=IntegrationEvent.Provider.TELEGRAM,
        status=IntegrationEvent.Status.SUCCESS,
        title="Telegram клиента привязан",
        payload={"user_id": profile.user_id, "chat_id": chat_id},
    )
    return {"ok": True, "handled": True, "client": True, "linked": True, "user_id": profile.user_id}


def _client_status_text(profile):
    return "\n".join(
        [
            "AurumWeb: Telegram подключен",
            "",
            f"Кабинет: {_absolute_url(reverse('client_portal:dashboard'))}",
            f"Уведомления: {'включены' if profile.telegram_notifications_enabled else 'выключены'}",
            "",
            "Команда /stop выключит уведомления. Включить их снова можно в личном кабинете.",
        ]
    )


def _client_response(chat_id, command, payload, message):
    profile = _bound_client_profile(chat_id)
    client = TelegramBotClient()

    if command in ("/start", "/link"):
        if payload:
            return _link_client_profile(chat_id, payload, message)
        if profile:
            sent = client.send_message(
                _client_status_text(profile), chat_id=chat_id, reply_markup=client.client_keyboard()
            )
            return {"ok": bool(sent), "handled": True, "client": True, "linked": True}
        return _link_client_profile(chat_id, "", message)

    if command == "/stop" and profile:
        profile.telegram_notifications_enabled = False
        profile.save(update_fields=("telegram_notifications_enabled",))
        sent = client.send_message(
            "Telegram-уведомления AurumWeb выключены. Включить их снова можно в личном кабинете.",
            chat_id=chat_id,
            reply_markup=client.client_keyboard(),
        )
        return {"ok": bool(sent), "handled": True, "client": True, "linked": True, "notifications_enabled": False}

    if command in ("/status", "/help") and profile:
        sent = client.send_message(_client_status_text(profile), chat_id=chat_id, reply_markup=client.client_keyboard())
        return {"ok": bool(sent), "handled": True, "client": True, "linked": True}

    if profile:
        client.send_message(
            "Команда не распознана. Доступные команды клиента: /status, /help, /stop.",
            chat_id=chat_id,
            reply_markup=client.client_keyboard(),
        )
    else:
        client.send_message(
            "Это бот AurumWeb. Зарегистрируйтесь по кнопке ниже или войдите в кабинет, чтобы подключить уведомления.",
            chat_id=chat_id,
            reply_markup=_telegram_entry_keyboard(chat_id, message),
        )
    return {"ok": True, "handled": True, "client": True, "linked": bool(profile), "reason": "unknown_command"}


def handle_telegram_update(update):
    callback = _extract_callback(update)
    message = _extract_message(update)
    if not message:
        return {"ok": True, "handled": False, "reason": "message_missing"}
    client = TelegramBotClient()
    chat_id = _chat_id(message)
    if not chat_id:
        return {"ok": True, "handled": False, "reason": "chat_missing"}
    if callback:
        client.answer_callback_query(callback.get("id"), "Готово")
        data = _callback_data(callback)
        command = _callback_command(data)
        payload = ""
        if not command:
            client.send_message("Кнопка устарела. Нажмите /help, чтобы открыть актуальное меню.", chat_id=chat_id)
            return {"ok": True, "handled": True, "callback": True, "reason": "unknown_callback"}
    else:
        text = (message.get("text") or "").strip()
        command, payload = _command_parts(text)
    if not command.startswith("/"):
        return _client_response(chat_id, "/link", text, message)

    if command in ("/start", "/link") and payload:
        return _client_response(chat_id, command, payload, message)

    if str(chat_id) != str(client.admin_chat_id):
        return _client_response(chat_id, command, payload, message)

    response_text = _admin_response(command)
    sent = client.send_message(response_text, chat_id=chat_id, reply_markup=client.admin_keyboard())
    return {"ok": bool(sent), "handled": True, "admin": True, "command": command}


def process_pending_telegram_updates(limit=20):
    config = TelegramBotSettings.load()
    client = TelegramBotClient()
    offset = config.last_update_id + 1 if config.last_update_id is not None else None
    updates = client.get_updates(limit=limit, offset=offset)
    handled = []
    max_update_id = config.last_update_id
    for update in updates.get("result", []):
        update_id = update.get("update_id")
        if update_id is not None and (max_update_id is None or update_id > max_update_id):
            max_update_id = update_id
        handled.append(handle_telegram_update(update))
    if max_update_id != config.last_update_id:
        config.last_update_id = max_update_id
        config.save(update_fields=("last_update_id", "updated_at"))
    return {
        "ok": bool(updates.get("ok")),
        "updates": len(updates.get("result", [])),
        "handled": handled,
        "last_update_id": config.last_update_id,
    }
