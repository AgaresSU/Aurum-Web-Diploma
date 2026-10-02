import json
import uuid
from urllib import request
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.models import User
from django.urls import reverse

from apps.accounts.models import Profile
from apps.integrations.models import IntegrationEvent, QuickConsultationBotSettings, TelegramBotSettings
from apps.integrations.receipt_images import build_quick_consultation_receipt_png


def _clip(value, limit=900):
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


class TelegramBotClient:
    webhook_route_name = "integrations:telegram-webhook"

    def __init__(self, token=None, admin_chat_id=None, webhook_secret=None, webhook_url=None, bot_username=None):
        self.config = self._load_db_config()
        config = self.config
        self.db_enabled = bool(config and config.is_enabled)
        self.token = (
            token
            if token is not None
            else self._first_configured(
                config.get_bot_token() if config else "",
                settings.TELEGRAM_BOT_TOKEN,
            )
        )
        self.bot_username = (
            bot_username
            if bot_username is not None
            else self._first_configured(
                config.bot_username if config else "",
                getattr(settings, "TELEGRAM_BOT_USERNAME", ""),
            )
        )
        self.admin_chat_id = (
            admin_chat_id
            if admin_chat_id is not None
            else self._first_configured(
                config.admin_chat_id if config else "",
                settings.TELEGRAM_ADMIN_CHAT_ID,
            )
        )
        self.webhook_secret = (
            webhook_secret
            if webhook_secret is not None
            else self._first_configured(
                config.get_webhook_secret() if config else "",
                settings.TELEGRAM_WEBHOOK_SECRET,
            )
        )
        self.webhook_url = (
            webhook_url
            if webhook_url is not None
            else self._first_configured(
                config.webhook_url if config else "",
                getattr(settings, "TELEGRAM_WEBHOOK_URL", ""),
            )
        )
        self.notifications_enabled = self.db_enabled or bool(settings.TELEGRAM_BOT_TOKEN)

    def _load_db_config(self):
        try:
            return TelegramBotSettings.objects.order_by("pk").first()
        except Exception:
            return None

    def _first_configured(self, *values):
        for value in values:
            if value:
                return value
        return ""

    @property
    def configured(self):
        return bool(self.token and self.admin_chat_id)

    @property
    def token_configured(self):
        return bool(self.token)

    def _clean_bot_username(self, value):
        return str(value or "").strip().removeprefix("@")

    def client_start_url(self, start_payload):
        username = self._clean_bot_username(self.bot_username)
        payload = str(start_payload or "").strip()
        if not username or not payload:
            return ""
        return f"https://t.me/{username}?start={quote(payload)}"

    def _record(self, status, title, payload=None):
        IntegrationEvent.objects.create(
            provider=IntegrationEvent.Provider.TELEGRAM,
            status=status,
            title=title,
            payload=payload or {},
        )

    def _absolute_url(self, path):
        base_url = settings.AURUMWEB_SITE_URL.rstrip("/")
        return f"{base_url}{path}" if base_url else path

    def _url_button(self, text, path):
        url = self._absolute_url(path)
        if not url.startswith(("http://", "https://")):
            return None
        return {"text": text, "url": url}

    def _callback_button(self, text, data):
        return {"text": text, "callback_data": data}

    def _inline_keyboard(self, rows):
        clean_rows = []
        for row in rows:
            clean_row = [button for button in row if button]
            if clean_row:
                clean_rows.append(clean_row)
        return {"inline_keyboard": clean_rows} if clean_rows else None

    def _reply_keyboard(self, rows, placeholder="Выберите действие"):
        clean_rows = []
        for row in rows:
            clean_row = [str(button).strip() for button in row if str(button or "").strip()]
            if clean_row:
                clean_rows.append(clean_row)
        if not clean_rows:
            return None
        return {
            "keyboard": clean_rows,
            "resize_keyboard": True,
            "one_time_keyboard": False,
            "input_field_placeholder": placeholder,
        }

    def _remove_keyboard(self):
        return {"remove_keyboard": True}

    def admin_keyboard(self, extra_rows=None):
        rows = extra_rows or []
        rows.extend(
            [
                [
                    self._callback_button("Сводка", "admin:status"),
                    self._callback_button("Заявки", "admin:leads"),
                ],
                [
                    self._callback_button("Диалоги", "admin:dialogs"),
                    self._callback_button("Сегодня", "admin:today"),
                ],
                [self._url_button("Открыть Office", reverse("office:dashboard"))],
            ]
        )
        return self._inline_keyboard(rows)

    def client_keyboard(self, extra_rows=None):
        rows = extra_rows or []
        rows.extend(
            [
                [self._url_button("Открыть кабинет", reverse("client_portal:dashboard"))],
                [
                    self._url_button("Диалоги", reverse("client_portal:conversations")),
                    self._url_button("Счета", reverse("client_portal:invoices")),
                ],
                [
                    self._url_button("Задачи", reverse("client_portal:orders")),
                    self._callback_button("Статус", "client:status"),
                ],
            ]
        )
        return self._inline_keyboard(rows)

    def _api_request(self, method, payload=None, timeout=8):
        if not self.token_configured:
            self._record(
                IntegrationEvent.Status.WARNING,
                "Telegram токен не настроен",
                {"method": method},
            )
            return {"ok": False, "error": "token_missing"}

        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=data, headers=headers)
        try:
            with request.urlopen(req, timeout=timeout) as response:
                body = response.read().decode("utf-8")
            result = json.loads(body or "{}")
            return result
        except Exception as exc:
            self._record(
                IntegrationEvent.Status.ERROR,
                "Ошибка Telegram API",
                {"method": method, "error": str(exc)},
            )
            return {"ok": False, "error": str(exc)}

    def _api_multipart_request(self, method, fields=None, files=None, timeout=12):
        if not self.token_configured:
            self._record(
                IntegrationEvent.Status.WARNING,
                "Telegram токен не настроен",
                {"method": method},
            )
            return {"ok": False, "error": "token_missing"}

        boundary = f"----AurumWebTelegram{uuid.uuid4().hex}"
        body = bytearray()
        fields = fields or {}
        files = files or {}
        for name, value in fields.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")
        for name, file_data in files.items():
            filename, content_type, content = file_data
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(
                (
                    f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                    f"Content-Type: {content_type}\r\n\r\n"
                ).encode()
            )
            body.extend(content)
            body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode())

        url = f"https://api.telegram.org/bot{self.token}/{method}"
        req = request.Request(
            url,
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with request.urlopen(req, timeout=timeout) as response:
                response_body = response.read().decode("utf-8")
            return json.loads(response_body or "{}")
        except Exception as exc:
            self._record(
                IntegrationEvent.Status.ERROR,
                "Ошибка Telegram API",
                {"method": method, "error": str(exc)},
            )
            return {"ok": False, "error": str(exc)}

    def get_me(self):
        result = self._api_request("getMe")
        username = (result.get("result") or {}).get("username") if result.get("ok") else ""
        if username:
            self.config = self.config or TelegramBotSettings.load()
            self.config.bot_username = self._clean_bot_username(username)
            self.config.save(update_fields=("bot_username", "updated_at"))
            self.bot_username = self.config.bot_username
        self._record(
            IntegrationEvent.Status.SUCCESS if result.get("ok") else IntegrationEvent.Status.ERROR,
            "Telegram getMe выполнен" if result.get("ok") else "Telegram getMe не выполнен",
            {"result": result},
        )
        return result

    def get_updates(self, limit=10, offset=None):
        payload = {"limit": limit}
        if offset is not None:
            payload["offset"] = offset
        result = self._api_request("getUpdates", payload)
        self._record(
            IntegrationEvent.Status.SUCCESS if result.get("ok") else IntegrationEvent.Status.ERROR,
            "Telegram updates получены" if result.get("ok") else "Telegram updates не получены",
            {"result": result},
        )
        return result

    def get_webhook_info(self):
        result = self._api_request("getWebhookInfo")
        self._record(
            IntegrationEvent.Status.SUCCESS if result.get("ok") else IntegrationEvent.Status.ERROR,
            "Telegram webhook info получен" if result.get("ok") else "Telegram webhook info не получен",
            {"result": result},
        )
        return result

    def set_webhook(
        self,
        webhook_url=None,
        secret=None,
        allowed_updates=None,
        drop_pending_updates=False,
        max_connections=None,
    ):
        if webhook_url is None:
            webhook_url = self.webhook_url
        if webhook_url is None or not webhook_url:
            base_url = getattr(settings, "AURUMWEB_SITE_URL", "").rstrip("/")
            if base_url:
                webhook_url = f"{base_url}{reverse(self.webhook_route_name)}"
        if not webhook_url:
            self._record(
                IntegrationEvent.Status.WARNING,
                "Telegram webhook URL не задан",
                {"reason": "webhook_url_missing"},
            )
            return {"ok": False, "error": "webhook_url_missing"}
        payload = {"url": webhook_url}
        webhook_secret = secret if secret is not None else self.webhook_secret
        if webhook_secret:
            payload["secret_token"] = webhook_secret
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates
        if drop_pending_updates:
            payload["drop_pending_updates"] = True
        if max_connections:
            payload["max_connections"] = max_connections
        result = self._api_request("setWebhook", payload)
        self._record(
            IntegrationEvent.Status.SUCCESS if result.get("ok") else IntegrationEvent.Status.ERROR,
            "Telegram webhook установлен" if result.get("ok") else "Telegram webhook не установлен",
            {"url": webhook_url, "result": result},
        )
        return result

    def delete_webhook(self, drop_pending_updates=False):
        result = self._api_request("deleteWebhook", {"drop_pending_updates": drop_pending_updates})
        self._record(
            IntegrationEvent.Status.SUCCESS if result.get("ok") else IntegrationEvent.Status.ERROR,
            "Telegram webhook удален" if result.get("ok") else "Telegram webhook не удален",
            {"result": result},
        )
        return result

    def set_my_name(self, name):
        result = self._api_request("setMyName", {"name": name})
        self._record(
            IntegrationEvent.Status.SUCCESS if result.get("ok") else IntegrationEvent.Status.ERROR,
            "Telegram имя бота обновлено" if result.get("ok") else "Telegram имя бота не обновлено",
            {"name": name, "result": result},
        )
        return result

    def set_my_description(self, description):
        result = self._api_request("setMyDescription", {"description": description})
        self._record(
            IntegrationEvent.Status.SUCCESS if result.get("ok") else IntegrationEvent.Status.ERROR,
            "Telegram описание бота обновлено" if result.get("ok") else "Telegram описание бота не обновлено",
            {"result": result},
        )
        return result

    def set_my_short_description(self, short_description):
        result = self._api_request("setMyShortDescription", {"short_description": short_description})
        self._record(
            IntegrationEvent.Status.SUCCESS if result.get("ok") else IntegrationEvent.Status.ERROR,
            (
                "Telegram короткое описание бота обновлено"
                if result.get("ok")
                else "Telegram короткое описание бота не обновлено"
            ),
            {"result": result},
        )
        return result

    def set_my_commands(self, commands):
        result = self._api_request("setMyCommands", {"commands": commands})
        self._record(
            IntegrationEvent.Status.SUCCESS if result.get("ok") else IntegrationEvent.Status.ERROR,
            "Telegram команды бота обновлены" if result.get("ok") else "Telegram команды бота не обновлены",
            {"commands": commands, "result": result},
        )
        return result

    def apply_aurumweb_branding(self):
        description = (
            "AurumWeb помогает обсудить сайт под ключ, поддержку после запуска, "
            "Python-автоматизацию и Telegram-ботов. Напишите задачу, и менеджер "
            "вернется с уточнениями, сроками и следующим шагом."
        )
        short_description = "Сайты под ключ, поддержка, Python и Telegram-боты."
        commands = [
            {"command": "start", "description": "Проверить подключение"},
            {"command": "status", "description": "Сводка Office"},
            {"command": "leads", "description": "Последние заявки"},
            {"command": "dialogs", "description": "Диалоги, где ждут ответа"},
            {"command": "today", "description": "Что требует внимания"},
            {"command": "help", "description": "Список команд"},
        ]
        results = {
            "name": self.set_my_name("AurumWeb"),
            "short_description": self.set_my_short_description(short_description),
            "description": self.set_my_description(description),
            "commands": self.set_my_commands(commands),
        }
        return {
            "ok": all(item.get("ok") for item in results.values()),
            "result": results,
        }

    def answer_callback_query(self, callback_query_id, text=""):
        if not callback_query_id:
            return {"ok": False, "error": "callback_query_id_missing"}
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        return self._api_request("answerCallbackQuery", payload)

    def send_prepared_method(self, method_payload):
        method_payload = method_payload or {}
        method = method_payload.get("method")
        if not method:
            return False
        payload = {key: value for key, value in method_payload.items() if key != "method"}
        result = self._api_request(method, payload)
        self._record(
            IntegrationEvent.Status.SUCCESS if result.get("ok") else IntegrationEvent.Status.ERROR,
            (
                "Telegram подготовленный ответ отправлен"
                if result.get("ok")
                else "Telegram подготовленный ответ не отправлен"
            ),
            {"method": method, "result": result},
        )
        return bool(result.get("ok"))

    def send_message(self, text, chat_id=None, reply_markup=None):
        target_chat_id = chat_id or self.admin_chat_id
        if not self.token_configured or not target_chat_id:
            IntegrationEvent.objects.create(
                provider=IntegrationEvent.Provider.TELEGRAM,
                status=IntegrationEvent.Status.WARNING,
                title="Telegram не настроен",
                payload={"has_token": bool(self.token), "has_chat_id": bool(target_chat_id), "text": text},
            )
            return False

        payload = {"chat_id": target_chat_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        result = self._api_request("sendMessage", payload)
        self._record(
            IntegrationEvent.Status.SUCCESS if result.get("ok") else IntegrationEvent.Status.ERROR,
            "Telegram уведомление отправлено" if result.get("ok") else "Ошибка Telegram",
            {"result": result},
        )
        return bool(result.get("ok"))

    def send_photo(self, photo_bytes, filename, caption="", chat_id=None, reply_markup=None):
        target_chat_id = chat_id or self.admin_chat_id
        if not self.token_configured or not target_chat_id:
            IntegrationEvent.objects.create(
                provider=IntegrationEvent.Provider.TELEGRAM,
                status=IntegrationEvent.Status.WARNING,
                title="Telegram не настроен",
                payload={"has_token": bool(self.token), "has_chat_id": bool(target_chat_id), "caption": caption},
            )
            return False

        fields = {"chat_id": target_chat_id}
        if caption:
            fields["caption"] = caption
        if reply_markup:
            fields["reply_markup"] = reply_markup
        result = self._api_multipart_request(
            "sendPhoto",
            fields=fields,
            files={"photo": (filename, "image/png", photo_bytes)},
        )
        self._record(
            IntegrationEvent.Status.SUCCESS if result.get("ok") else IntegrationEvent.Status.ERROR,
            "Telegram изображение отправлено" if result.get("ok") else "Ошибка Telegram",
            {"result": result},
        )
        return bool(result.get("ok"))

    def send_photo_url(self, photo_url, caption="", chat_id=None, reply_markup=None):
        target_chat_id = chat_id or self.admin_chat_id
        if not self.token_configured or not target_chat_id:
            IntegrationEvent.objects.create(
                provider=IntegrationEvent.Provider.TELEGRAM,
                status=IntegrationEvent.Status.WARNING,
                title="Telegram не настроен",
                payload={"has_token": bool(self.token), "has_chat_id": bool(target_chat_id), "caption": caption},
            )
            return False

        payload = {"chat_id": target_chat_id, "photo": photo_url}
        if caption:
            payload["caption"] = caption
        if reply_markup:
            payload["reply_markup"] = reply_markup
        result = self._api_request("sendPhoto", payload)
        self._record(
            IntegrationEvent.Status.SUCCESS if result.get("ok") else IntegrationEvent.Status.ERROR,
            "Telegram карточка отправлена" if result.get("ok") else "Ошибка Telegram",
            {"result": result},
        )
        return bool(result.get("ok"))

    def _client_profile(self, user=None, email=""):
        if user:
            profile = Profile.objects.filter(user=user).first()
            if profile:
                return profile
        email = (email or "").strip()
        if email:
            user = User.objects.filter(email__iexact=email).first()
            if user:
                return Profile.objects.filter(user=user).first()
        return None

    def _send_client_notification(self, text, user=None, email="", profile=None, payload=None, extra_rows=None):
        if not self.notifications_enabled:
            self._record(
                IntegrationEvent.Status.WARNING,
                "Telegram уведомления выключены",
                payload or {},
            )
            return False

        target_profile = profile or self._client_profile(user=user, email=email)
        if not target_profile:
            self._record(
                IntegrationEvent.Status.INFO,
                "Telegram клиента не подключен",
                {**(payload or {}), "reason": "profile_missing"},
            )
            return False
        if not target_profile.telegram_chat_id:
            self._record(
                IntegrationEvent.Status.INFO,
                "Telegram клиента не подключен",
                {**(payload or {}), "user_id": target_profile.user_id, "reason": "chat_missing"},
            )
            return False
        if not target_profile.telegram_notifications_enabled:
            self._record(
                IntegrationEvent.Status.INFO,
                "Telegram уведомления клиента выключены",
                {**(payload or {}), "user_id": target_profile.user_id},
            )
            return False
        return self.send_message(
            text,
            chat_id=target_profile.telegram_chat_id,
            reply_markup=self.client_keyboard(extra_rows=extra_rows),
        )

    def notify_client_request_created(self, lead, conversation=None):
        conversation_path = ""
        if conversation:
            conversation_path = self._absolute_url(
                reverse("client_portal:conversation-detail", kwargs={"token": conversation.public_token})
            )
        lines = [
            "Заявка создана в AurumWeb",
            f"Тема: {lead.subject or lead.service_type or 'Новая задача'}",
            "Менеджер увидит задачу и ответит в диалоге.",
        ]
        if conversation_path:
            lines.append(f"Диалог: {conversation_path}")
        return self._send_client_notification(
            "\n".join(lines),
            user=lead.user,
            email=lead.email,
            payload={"lead_id": lead.pk, "conversation_id": conversation.pk if conversation else None},
            extra_rows=(
                [
                    [
                        self._url_button(
                            "Открыть диалог",
                            reverse("client_portal:conversation-detail", kwargs={"token": conversation.public_token}),
                        )
                    ]
                ]
                if conversation
                else None
            ),
        )

    def notify_client_manager_message(self, conversation, message):
        conversation_path = self._absolute_url(
            reverse("client_portal:conversation-detail", kwargs={"token": conversation.public_token})
        )
        text = (
            "Новое сообщение от менеджера AurumWeb\n"
            f"Диалог: {conversation.title or f'#{conversation.pk}'}\n"
            f"Открыть: {conversation_path}\n\n"
            f"{_clip(message.body)}"
        )
        user = conversation.user or (
            conversation.lead.user if conversation.lead_id and conversation.lead.user_id else None
        )
        return self._send_client_notification(
            text,
            user=user,
            email=conversation.client_email,
            payload={"conversation_id": conversation.pk, "message_id": message.pk},
            extra_rows=[
                [
                    self._url_button(
                        "Открыть диалог",
                        reverse("client_portal:conversation-detail", kwargs={"token": conversation.public_token}),
                    )
                ]
            ],
        )

    def notify_client_invoice_issued(self, invoice):
        pay_path = self._absolute_url(reverse("billing:pay-invoice", kwargs={"token": invoice.public_token}))
        text = (
            "Выставлен счет AurumWeb\n"
            f"Счет: #{invoice.pk} — {invoice.title}\n"
            f"Сумма: {invoice.amount} ₽\n"
            f"Оплатить: {pay_path}"
        )
        return self._send_client_notification(
            text,
            user=invoice.user,
            email=invoice.client_email,
            payload={"invoice_id": invoice.pk},
            extra_rows=[
                [
                    self._url_button(
                        "Оплатить счет",
                        reverse("billing:pay-invoice", kwargs={"token": invoice.public_token}),
                    )
                ],
                [
                    self._url_button(
                        "Открыть счет",
                        reverse("billing:invoice-detail", kwargs={"token": invoice.public_token}),
                    )
                ],
            ],
        )

    def notify_client_payment_succeeded(self, payment):
        invoice = payment.invoice
        invoice_path = self._absolute_url(
            reverse("client_portal:invoice-detail", kwargs={"token": invoice.public_token})
        )
        text = (
            "Оплата счета AurumWeb подтверждена\n"
            f"Счет: #{invoice.pk} — {invoice.title}\n"
            f"Сумма: {payment.amount} ₽\n"
            "Статус: оплачен\n"
            "Чек: Robokassa отправит фискальный чек отдельным письмом на email, указанный при оплате.\n"
            f"Открыть: {invoice_path}"
        )
        return self._send_client_notification(
            text,
            user=invoice.user,
            email=invoice.client_email,
            payload={"invoice_id": invoice.pk, "payment_id": payment.pk},
            extra_rows=[
                [
                    self._url_button(
                        "Открыть счет",
                        reverse("client_portal:invoice-detail", kwargs={"token": invoice.public_token}),
                    )
                ]
            ],
        )

    def notify_client_order_updated(self, order, previous_status=None):
        order_path = self._absolute_url(reverse("client_portal:order-detail", kwargs={"pk": order.pk}))
        status_line = order.get_status_display()
        if previous_status and previous_status != order.status:
            previous_label = dict(order.Status.choices).get(previous_status, previous_status)
            status_line = f"{previous_label} -> {order.get_status_display()}"
        text = (
            f"Обновлен заказ AurumWeb\nЗаказ: #{order.pk} — {order.title}\nСтатус: {status_line}\nОткрыть: {order_path}"
        )
        return self._send_client_notification(
            text,
            user=order.user,
            email=order.client_email,
            payload={"order_id": order.pk, "previous_status": previous_status, "status": order.status},
            extra_rows=[
                [self._url_button("Открыть заказ", reverse("client_portal:order-detail", kwargs={"pk": order.pk}))]
            ],
        )

    def notify_client_site_created(self, site):
        site_path = self._absolute_url(reverse("client_portal:site-detail", kwargs={"pk": site.pk}))
        email = site.order.client_email if site.order_id else ""
        text = f"Карточка сайта создана\nСайт: {site.title}\nСтатус: {site.get_status_display()}\nОткрыть: {site_path}"
        return self._send_client_notification(
            text,
            user=site.user,
            email=email,
            payload={"site_id": site.pk, "order_id": site.order_id},
            extra_rows=[
                [self._url_button("Открыть сайт", reverse("client_portal:site-detail", kwargs={"pk": site.pk}))]
            ],
        )

    def notify_client_site_updated(self, site, changed_fields=None, previous_status=None):
        site_path = self._absolute_url(reverse("client_portal:site-detail", kwargs={"pk": site.pk}))
        email = site.order.client_email if site.order_id else ""
        changes = ", ".join(changed_fields or [])
        lines = [
            "Обновлена карточка сайта",
            f"Сайт: {site.title}",
            f"Статус: {site.get_status_display()}",
        ]
        if previous_status and previous_status != site.status:
            previous_label = dict(site.Status.choices).get(previous_status, previous_status)
            lines.append(f"Изменение статуса: {previous_label} -> {site.get_status_display()}")
        if changes:
            lines.append(f"Обновлено: {changes}")
        lines.append(f"Открыть: {site_path}")
        return self._send_client_notification(
            "\n".join(lines),
            user=site.user,
            email=email,
            payload={"site_id": site.pk, "changed_fields": changed_fields or []},
            extra_rows=[
                [self._url_button("Открыть сайт", reverse("client_portal:site-detail", kwargs={"pk": site.pk}))]
            ],
        )

    def notify_client_test(self, profile):
        dashboard_path = self._absolute_url(reverse("client_portal:dashboard"))
        text = (
            "AurumWeb: тестовое уведомление\n"
            "Telegram подключен к личному кабинету. Сюда будут приходить сообщения по заявкам, "
            "диалогам, счетам, оплатам, заказам и сайту.\n"
            f"Кабинет: {dashboard_path}"
        )
        return self._send_client_notification(
            text,
            profile=profile,
            payload={"user_id": profile.user_id, "kind": "client_test"},
        )

    def notify_client_deadline_reminder(self, user, payload):
        path = payload.get("path") or "/client/"
        deadline_path = self._absolute_url(path)
        text = (
            f"Напоминание AurumWeb\n{payload['title']}\n"
            f"Дата: {payload.get('deadline_display') or payload['deadline']}\n"
            f"Осталось дней: {payload['days_left']}\n"
            f"Открыть: {deadline_path}"
        )
        return self._send_client_notification(
            text,
            user=user,
            email=payload.get("email", ""),
            payload={"kind": "deadline_reminder", **payload},
            extra_rows=[[self._url_button("Открыть", path)]],
        )

    def notify_client_project_completed(self, project, user=None):
        path = reverse("client_portal:project-detail", kwargs={"token": project.public_id})
        project_path = self._absolute_url(path)
        text = "Проект AurumWeb завершен\n" f"Проект: {project.title}\n" "Статус: завершен\n" f"Открыть: {project_path}"
        return self._send_client_notification(
            text,
            user=user or project.user,
            email=project.client_email,
            payload={"kind": "project_completed", "project_id": project.pk},
            extra_rows=[[self._url_button("Открыть проект", path)]],
        )

    def notify_new_lead(self, lead, conversation=None):
        if not self.notifications_enabled:
            self._record(
                IntegrationEvent.Status.WARNING,
                "Telegram уведомления выключены",
                {"lead_id": lead.pk},
            )
            return False
        lead_path = self._absolute_url(reverse("office:lead-detail", kwargs={"pk": lead.pk}))
        lines = [
            "Новая заявка AurumWeb",
            f"Заявка: #{lead.pk}",
            f"Имя: {lead.name or '-'}",
            f"Email: {lead.email or '-'}",
            f"Тип: {lead.service_type or '-'}",
            f"Тема: {lead.subject or '-'}",
            f"Office: {lead_path}",
        ]
        if conversation:
            conversation_path = self._absolute_url(
                reverse("office:conversation-detail", kwargs={"pk": conversation.pk})
            )
            lines.append(f"Диалог: {conversation_path}")
        lines.extend(["", _clip(lead.task)])
        extra_rows = [[self._url_button("Открыть заявку", reverse("office:lead-detail", kwargs={"pk": lead.pk}))]]
        if conversation:
            extra_rows.append(
                [
                    self._url_button(
                        "Открыть диалог", reverse("office:conversation-detail", kwargs={"pk": conversation.pk})
                    )
                ]
            )
        return self.send_message("\n".join(lines), reply_markup=self.admin_keyboard(extra_rows=extra_rows))

    def notify_client_message(self, conversation, message):
        if not self.notifications_enabled:
            self._record(
                IntegrationEvent.Status.WARNING,
                "Telegram уведомления выключены",
                {"conversation_id": conversation.pk, "message_id": message.pk},
            )
            return False
        conversation_path = self._absolute_url(reverse("office:conversation-detail", kwargs={"pk": conversation.pk}))
        text = (
            "Новое сообщение клиента\n"
            f"Диалог: {conversation.title or f'#{conversation.pk}'}\n"
            f"Email: {conversation.client_email or '-'}\n"
            f"Office: {conversation_path}\n\n"
            f"{_clip(message.body)}"
        )
        return self.send_message(
            text,
            reply_markup=self.admin_keyboard(
                [
                    [
                        self._url_button(
                            "Открыть диалог", reverse("office:conversation-detail", kwargs={"pk": conversation.pk})
                        )
                    ]
                ]
            ),
        )

    def notify_invoice_issued(self, invoice):
        if not self.notifications_enabled:
            self._record(
                IntegrationEvent.Status.WARNING,
                "Telegram уведомления выключены",
                {"invoice_id": invoice.pk},
            )
            return False
        invoice_path = self._absolute_url(reverse("office:invoice-detail", kwargs={"pk": invoice.pk}))
        public_path = self._absolute_url(reverse("billing:invoice-detail", kwargs={"token": invoice.public_token}))
        text = (
            "Счет выставлен\n"
            f"Счет: #{invoice.pk}\n"
            f"Клиент: {invoice.client_name or '-'}\n"
            f"Email: {invoice.client_email or '-'}\n"
            f"Сумма: {invoice.amount} ₽\n"
            f"Office: {invoice_path}\n"
            f"Ссылка клиенту: {public_path}"
        )
        return self.send_message(
            text,
            reply_markup=self.admin_keyboard(
                [[self._url_button("Открыть счет", reverse("office:invoice-detail", kwargs={"pk": invoice.pk}))]]
            ),
        )

    def notify_payment_succeeded(self, payment):
        if not self.notifications_enabled:
            self._record(
                IntegrationEvent.Status.WARNING,
                "Telegram уведомления выключены",
                {"invoice_id": payment.invoice_id, "payment_id": payment.pk},
            )
            return False
        invoice = payment.invoice
        invoice_path = self._absolute_url(reverse("office:invoice-detail", kwargs={"pk": invoice.pk}))
        text = (
            "Оплата получена\n"
            f"Счет: #{invoice.pk} — {invoice.title}\n"
            f"Клиент: {invoice.client_name or '-'}\n"
            f"Сумма: {payment.amount} ₽\n"
            f"Провайдер: {payment.get_provider_display()}\n"
            f"Office: {invoice_path}"
        )
        return self.send_message(
            text,
            reply_markup=self.admin_keyboard(
                [[self._url_button("Открыть счет", reverse("office:invoice-detail", kwargs={"pk": invoice.pk}))]]
            ),
        )

    def notify_payment_failed(self, payment, reason, payload=None):
        if not self.notifications_enabled:
            self._record(
                IntegrationEvent.Status.WARNING,
                "Telegram уведомления выключены",
                {"invoice_id": payment.invoice_id, "payment_id": payment.pk, "reason": reason},
            )
            return False
        invoice = payment.invoice
        invoice_path = self._absolute_url(reverse("office:invoice-detail", kwargs={"pk": invoice.pk}))
        text = (
            "Проблема с оплатой\n"
            f"Счет: #{invoice.pk} — {invoice.title}\n"
            f"Платеж: #{payment.pk}\n"
            f"Причина: {reason}\n"
            f"Office: {invoice_path}"
        )
        self._record(
            IntegrationEvent.Status.ERROR,
            "Telegram уведомление о проблеме оплаты",
            {"invoice_id": invoice.pk, "payment_id": payment.pk, "reason": reason, "payload": payload or {}},
        )
        return self.send_message(
            text,
            reply_markup=self.admin_keyboard(
                [[self._url_button("Открыть счет", reverse("office:invoice-detail", kwargs={"pk": invoice.pk}))]]
            ),
        )


class QuickConsultationBotClient(TelegramBotClient):
    webhook_route_name = "integrations:quick-telegram-webhook"

    def __init__(self, token=None, admin_chat_id=None, webhook_secret=None, webhook_url=None, bot_username=None):
        self.config = self._load_db_config()
        config = self.config
        self.db_enabled = bool(config and config.is_enabled)
        self.token = (
            token
            if token is not None
            else self._first_configured(
                config.get_bot_token() if config else "",
                getattr(settings, "QUICK_TELEGRAM_BOT_TOKEN", ""),
            )
        )
        self.bot_username = (
            bot_username
            if bot_username is not None
            else self._first_configured(
                config.bot_username if config else "",
                getattr(settings, "QUICK_TELEGRAM_BOT_USERNAME", ""),
            )
        )
        self.admin_chat_id = (
            admin_chat_id
            if admin_chat_id is not None
            else self._first_configured(
                config.admin_chat_id if config else "",
                getattr(settings, "QUICK_TELEGRAM_ADMIN_CHAT_ID", ""),
                settings.TELEGRAM_ADMIN_CHAT_ID,
            )
        )
        self.webhook_secret = (
            webhook_secret
            if webhook_secret is not None
            else self._first_configured(
                config.get_webhook_secret() if config else "",
                getattr(settings, "QUICK_TELEGRAM_WEBHOOK_SECRET", ""),
            )
        )
        self.webhook_url = (
            webhook_url
            if webhook_url is not None
            else self._first_configured(
                config.webhook_url if config else "",
                getattr(settings, "QUICK_TELEGRAM_WEBHOOK_URL", ""),
            )
        )
        self.notifications_enabled = self.db_enabled or bool(getattr(settings, "QUICK_TELEGRAM_BOT_TOKEN", ""))

    def _load_db_config(self):
        try:
            return QuickConsultationBotSettings.objects.order_by("pk").first()
        except Exception:
            return None

    def quick_consultation_start_url(self):
        return self.client_start_url("consult")

    def quick_card_url(self, filename):
        filename = str(filename or "").strip().lstrip("/")
        static_url = settings.STATIC_URL.rstrip("/")
        url = self._absolute_url(f"{static_url}/bot/{filename}")
        version = str(getattr(settings, "QUICK_TELEGRAM_CARD_VERSION", "") or "").strip()
        if not version:
            return url
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}v={quote(version)}"

    def set_webhook(
        self,
        webhook_url=None,
        secret=None,
        allowed_updates=None,
        drop_pending_updates=False,
        max_connections=None,
    ):
        return super().set_webhook(
            webhook_url=webhook_url,
            secret=secret,
            allowed_updates=allowed_updates or ["message", "callback_query"],
            drop_pending_updates=drop_pending_updates,
            max_connections=max_connections or 4,
        )

    def quick_consultation_intro_keyboard(self):
        return self._remove_keyboard()

    def quick_consultation_cancel_keyboard(self):
        return self._inline_keyboard([[self._callback_button("Меню", "quick:menu")]])

    def quick_consultation_payment_keyboard(self, payment_url):
        return self._inline_keyboard([[{"text": "Оплатить услугу", "url": payment_url}]])

    def quick_amount_keyboard(self):
        return self._inline_keyboard([[self._callback_button("Меню", "quick:menu")]])

    def quick_consultation_offer_keyboard(self):
        return self._inline_keyboard(
            [
                [self._callback_button("Оплатить 5 000 ₽", "quick:pay_fixed")],
                [self._callback_button("Указать свою сумму", "quick:custom_amount")],
                [self._callback_button("Меню", "quick:menu")],
            ]
        )

    def quick_main_menu_keyboard(self):
        return self._inline_keyboard(
            [
                [
                    self._callback_button("Оплатить услугу", "quick:pay"),
                    self._callback_button("Проконсультироваться", "quick:start"),
                ],
                [
                    self._callback_button("Мои оплаты", "quick:payments"),
                    self._callback_button("Изменить email", "quick:edit_email"),
                ],
                [
                    self._url_button("Оферта", reverse("fast-legal-page", kwargs={"slug": "offer"})),
                    self._url_button("Политика", reverse("fast-legal-page", kwargs={"slug": "privacy"})),
                ],
                [self._callback_button("Контакты", "quick:contacts")],
            ]
        )

    def quick_offer_keyboard(self):
        return self._inline_keyboard(
            [
                [self._url_button("Открыть оферту", reverse("fast-legal-page", kwargs={"slug": "offer"}))],
                [self._callback_button("Принимаю оферту", "quick:accept_offer")],
                [self._callback_button("Отклонить", "quick:cancel")],
            ]
        )

    def quick_privacy_keyboard(self):
        return self._inline_keyboard(
            [
                [self._url_button("Открыть политику", reverse("fast-legal-page", kwargs={"slug": "privacy"}))],
                [self._callback_button("Принимаю политику", "quick:accept_privacy")],
                [self._callback_button("Назад к оферте", "quick:back_offer")],
                [self._callback_button("Отклонить", "quick:cancel")],
            ]
        )

    def quick_email_keyboard(self):
        return self._inline_keyboard(
            [
                [self._callback_button("Назад к политике", "quick:back_privacy")],
                [self._callback_button("Отклонить", "quick:cancel")],
            ]
        )

    def quick_email_confirm_keyboard(self):
        return self._inline_keyboard(
            [
                [self._callback_button("Подтвердить email", "quick:confirm_email")],
                [self._callback_button("Изменить email", "quick:edit_email")],
                [self._callback_button("Назад", "quick:back_privacy")],
            ]
        )

    def quick_contacts_keyboard(self):
        return self._inline_keyboard(
            [
                [self._url_button("Открыть контакты", reverse("fast-legal-page", kwargs={"slug": "contacts"}))],
                [self._callback_button("Меню", "quick:menu")],
            ]
        )

    def quick_payments_keyboard(self, consultation=None):
        rows = []
        if consultation and consultation.invoice_id:
            rows.append(
                [
                    self._url_button(
                        "Открыть последний счет",
                        reverse("billing:invoice-detail", kwargs={"token": consultation.invoice.public_token}),
                    )
                ]
            )
            rows.append(
                [
                    self._url_button(
                        "Оплатить последний счет",
                        reverse("billing:pay-invoice", kwargs={"token": consultation.invoice.public_token}),
                    )
                ]
            )
        rows.append([self._callback_button("Меню", "quick:menu")])
        return self._inline_keyboard(rows)

    def send_quick_card(self, card_filename, caption, chat_id, reply_markup=None):
        return self.send_photo_url(
            self.quick_card_url(card_filename),
            caption=caption,
            chat_id=chat_id,
            reply_markup=reply_markup,
        )

    def notify_quick_consultation_created(self, consultation):
        if not self.notifications_enabled:
            self._record(
                IntegrationEvent.Status.WARNING,
                "Telegram уведомления выключены",
                {"quick_consultation_id": consultation.pk},
            )
            return False
        consultation_path = self._absolute_url(
            reverse("office:quick-consultation-detail", kwargs={"pk": consultation.pk})
        )
        text = (
            "Новая быстрая консультация\n"
            f"Заявка: #{consultation.pk}\n"
            f"Клиент: {consultation.display_name}\n"
            f"Telegram: {consultation.telegram_label}\n"
            f"Office: {consultation_path}\n\n"
            f"{_clip(consultation.question)}"
        )
        return self.send_message(
            text,
            reply_markup=self._inline_keyboard(
                [
                    [
                        self._url_button(
                            "Открыть консультацию",
                            reverse("office:quick-consultation-detail", kwargs={"pk": consultation.pk}),
                        )
                    ]
                ]
            ),
        )

    def send_quick_consultation_reply(self, consultation, text):
        return self.send_message(_clip(text, 1200), chat_id=consultation.telegram_chat_id)

    def send_quick_consultation_payment_link(self, consultation, invoice, payment_url):
        text = (
            "Счет за услугу AurumWeb сформирован.\n"
            f"Услуга: {invoice.title}\n"
            f"Сумма: {invoice.amount} ₽\n"
            "Нажмите кнопку ниже, чтобы перейти к оплате через Robokassa."
        )
        return self.send_message(
            text,
            chat_id=consultation.telegram_chat_id,
            reply_markup=self.quick_consultation_payment_keyboard(payment_url),
        )

    def notify_quick_consultation_paid(self, consultation, payment):
        caption = "Оплата прошла успешно."
        try:
            receipt_png = build_quick_consultation_receipt_png(consultation, payment)
        except Exception as exc:
            self._record(
                IntegrationEvent.Status.ERROR,
                "Квитанция AurumWeb не сформирована",
                {"quick_consultation_id": consultation.pk, "payment_id": payment.pk, "error": str(exc)},
            )
            return self.send_message(caption, chat_id=consultation.telegram_chat_id)

        sent = self.send_photo(
            receipt_png,
            "aurumweb-payment-receipt.png",
            caption=caption,
            chat_id=consultation.telegram_chat_id,
        )
        if sent:
            return True
        return self.send_message(caption, chat_id=consultation.telegram_chat_id)

    def apply_aurumweb_branding(self):
        description = (
            "AurumWeb Fast: консультации, разработка, поддержка сайтов, приложений и Telegram-ботов "
            "с оплатой через Robokassa."
        )
        short_description = "Быстрые услуги AurumWeb."
        commands = [{"command": "start", "description": "Обновить меню"}]
        results = {
            "name": self.set_my_name("AurumWeb Consult"),
            "short_description": self.set_my_short_description(short_description),
            "description": self.set_my_description(description),
            "commands": self.set_my_commands(commands),
        }
        return {
            "ok": all(item.get("ok") for item in results.values()),
            "result": results,
        }
