import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import Profile
from apps.content.models import TemplateProduct
from apps.integrations.models import TelegramBotSettings
from apps.leads.models import Lead
from apps.messaging.models import Conversation, Message


class LeadApiTests(TestCase):
    def setUp(self):
        cache.clear()

    def _telegram_response(self, payload):
        class Response:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, exc_type, exc, traceback):
                return False

            def read(self_inner):
                return json.dumps(payload).encode("utf-8")

        return Response()

    def _enable_telegram(self):
        config = TelegramBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.bot_username = "aurumweb_bot"
        config.save()

    def _telegram_payloads(self, urlopen):
        payloads = []
        for call in urlopen.call_args_list:
            request_obj = call.args[0]
            if request_obj.data:
                payloads.append(json.loads(request_obj.data.decode("utf-8")))
        return payloads

    def test_lead_api_rate_limit(self):
        payload = {
            "name": "Client",
            "email": "client@example.test",
            "task": "Нужен сайт",
            "personal_data_consent": "on",
        }
        for _ in range(8):
            response = self.client.post(
                "/api/leads/",
                data=json.dumps(payload),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 201)

        response = self.client.post(
            "/api/leads/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 429)

    def test_lead_api_rejects_oversized_payload(self):
        payload = {
            "name": "Client",
            "email": "client@example.test",
            "task": "x" * (70 * 1024),
            "personal_data_consent": "on",
        }

        response = self.client.post(
            "/api/leads/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 413)
        self.assertFalse(Lead.objects.exists())

    def test_lead_api_rejects_overlong_task(self):
        payload = {
            "name": "Client",
            "email": "client@example.test",
            "task": "x" * 8001,
            "personal_data_consent": "on",
        }

        response = self.client.post(
            "/api/leads/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Lead.objects.exists())

    def test_lead_api_rejects_cross_origin_browser_post(self):
        payload = {
            "name": "Client",
            "email": "client@example.test",
            "task": "Нужен сайт",
            "personal_data_consent": "on",
        }

        response = self.client.post(
            "/api/leads/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_ORIGIN="https://evil.example",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Lead.objects.exists())

    @override_settings(
        DEBUG=True,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
        AURUMWEB_SITE_URL="https://aurumweb.test",
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_lead_api_notifies_linked_client_by_email(self, urlopen):
        self._enable_telegram()
        user = User.objects.create_user(
            username="client",
            email="client@example.test",
            password="StrongPass123!",
        )
        Profile.objects.create(
            user=user,
            role=Profile.Role.CLIENT,
            telegram_chat_id="999",
            telegram_notifications_enabled=True,
        )
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 1}})

        response = self.client.post(
            "/api/leads/",
            data=json.dumps(
                {
                    "name": "Client",
                    "email": "client@example.test",
                    "subject": "Сайт под ключ",
                    "task": "Нужен сайт, поддержка и Telegram-уведомления.",
                    "personal_data_consent": "on",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        response_payload = response.json()
        self.assertIn("Заявка принята", response_payload["message"])
        conversation = Conversation.objects.get()
        self.assertEqual(conversation.messages.count(), 2)
        auto_reply = conversation.messages.get(author_role=Message.AuthorRole.SYSTEM)
        self.assertIn("Заявка принята", auto_reply.body)
        payloads = self._telegram_payloads(urlopen)
        client_payload = next(payload for payload in payloads if str(payload["chat_id"]) == "999")
        self.assertIn("Заявка создана в AurumWeb", client_payload["text"])
        self.assertIn("https://aurumweb.test/client/conversations/", client_payload["text"])

    def test_template_demo_request_creates_conversation(self):
        template = TemplateProduct.objects.create(
            title="Юридический сайт",
            slug="legal-demo",
            template_type=TemplateProduct.TemplateType.WEBSITE,
            category="Финансы и право",
            industry="Юридическая практика",
            short_description="Демо сайта юридического эксперта.",
            conversion_focus="Получить квалифицированную заявку",
        )

        response = self.client.post(
            f"/api/leads/templates/{template.slug}/request/",
            {
                "request_title": "Получить разбор юридического сайта",
                "request_field_label_1": "Сфера практики",
                "request_field_value_1": "Арбитраж",
                "client_name": "Ирина",
                "reply_contact": "irina@example.test",
                "comment": "Нужен сайт и поддержка.",
                "personal_data_consent": "on",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("/messenger/c/", payload["redirect_url"])
        lead = Lead.objects.get(email="irina@example.test")
        conversation = Conversation.objects.get(lead=lead)
        self.assertEqual(conversation.client_email, "irina@example.test")
        self.assertEqual(conversation.messages.count(), 2)
        self.assertTrue(conversation.messages.filter(author_role=Message.AuthorRole.SYSTEM).exists())
        self.assertIn("Сфера практики: Арбитраж", lead.task)
        self.assertIn("/template-demos/legal-demo/", lead.task)
        self.assertEqual(lead.metadata["selected_template"]["slug"], template.slug)
        self.assertTrue(lead.metadata["selected_template"]["demo_url"].endswith("/template-demos/legal-demo/"))

    def test_template_demo_request_accepts_legacy_field_names(self):
        template = TemplateProduct.objects.create(
            title="Сайт кафе",
            slug="cafe-demo",
            template_type=TemplateProduct.TemplateType.WEBSITE,
            category="Еда и гостеприимство",
            industry="Кафе",
            short_description="Демо сайта кафе.",
        )

        response = self.client.post(
            f"/api/leads/templates/{template.slug}/request/",
            {
                "field_1": "Банкеты",
                "name": "Олег",
                "contact": "oleg@example.test",
                "comment": "Хочу заявки с демо.",
                "personal_data_consent": "on",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 201)
        lead = Lead.objects.get(email="oleg@example.test")
        self.assertIn("Поле 1: Банкеты", lead.task)

    def test_template_demo_request_requires_consent(self):
        template = TemplateProduct.objects.create(
            title="Сайт сервиса",
            slug="service-demo",
            template_type=TemplateProduct.TemplateType.WEBSITE,
            category="Локальные услуги",
            industry="Сервис",
            short_description="Демо сайта сервиса.",
        )

        response = self.client.post(
            f"/api/leads/templates/{template.slug}/request/",
            {
                "name": "Олег",
                "contact": "oleg@example.test",
                "comment": "Хочу заявки с демо.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Lead.objects.exists())

    def test_template_demo_request_rejects_cross_origin_post(self):
        template = TemplateProduct.objects.create(
            title="Сайт сервиса",
            slug="cross-origin-demo",
            template_type=TemplateProduct.TemplateType.WEBSITE,
            category="Локальные услуги",
            industry="Сервис",
            short_description="Демо сайта сервиса.",
        )

        response = self.client.post(
            f"/api/leads/templates/{template.slug}/request/",
            {
                "name": "Олег",
                "contact": "oleg@example.test",
                "comment": "Хочу заявки с демо.",
                "personal_data_consent": "on",
            },
            HTTP_ORIGIN="https://evil.example",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Lead.objects.exists())
