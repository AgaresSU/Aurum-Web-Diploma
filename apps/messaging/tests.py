import json
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from apps.messaging.models import Conversation, Message


class MessageApiTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_message_api_rate_limit(self):
        payload = {"message": "Здравствуйте", "title": "Диалог с сайта", "personal_data_consent": "on"}
        for _ in range(20):
            response = self.client.post(
                "/messenger/api/messages/",
                data=json.dumps(payload),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)

        response = self.client.post(
            "/messenger/api/messages/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 429)

    def test_message_api_requires_consent(self):
        response = self.client.post(
            "/messenger/api/messages/",
            data=json.dumps({"message": "Здравствуйте", "title": "Диалог с сайта"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_message_api_rejects_overlong_body(self):
        response = self.client.post(
            "/messenger/api/messages/",
            data=json.dumps({"message": "x" * 4001, "title": "Диалог с сайта", "personal_data_consent": "on"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_message_api_creates_public_conversation_token(self):
        response = self.client.post(
            "/messenger/api/messages/",
            data=json.dumps(
                {
                    "message": "Хочу обсудить сайт и поддержку.",
                    "title": "AurumWeb",
                    "email": "client@example.test",
                    "personal_data_consent": "on",
                }
            ),
            content_type="application/json",
            HTTP_ORIGIN="http://testserver",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        conversation = Conversation.objects.get()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["conversation_token"], str(conversation.public_token))
        self.assertIn("Заявка принята", payload["auto_reply"])
        self.assertEqual(conversation.client_email, "client@example.test")
        self.assertEqual(conversation.messages.count(), 2)
        self.assertTrue(conversation.messages.filter(author_role=Message.AuthorRole.SYSTEM).exists())

    def test_message_api_does_not_duplicate_auto_reply_for_existing_conversation(self):
        conversation = Conversation.objects.create(
            client_email="client@example.test",
            title="Диалог с сайта",
        )

        response = self.client.post(
            "/messenger/api/messages/",
            data=json.dumps(
                {
                    "conversation_token": str(conversation.public_token),
                    "message": "Дополнение по задаче.",
                    "title": "AurumWeb",
                    "email": "client@example.test",
                    "personal_data_consent": "on",
                }
            ),
            content_type="application/json",
            HTTP_ORIGIN="http://testserver",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["auto_reply"], "")
        self.assertEqual(conversation.messages.filter(author_role=Message.AuthorRole.SYSTEM).count(), 0)

    def test_public_conversation_never_renders_internal_messages(self):
        conversation = Conversation.objects.create(client_email="client@example.test", title="Диалог")
        Message.objects.create(
            conversation=conversation,
            author_role=Message.AuthorRole.MANAGER,
            body="Ответ клиенту",
        )
        Message.objects.create(
            conversation=conversation,
            author_role=Message.AuthorRole.MANAGER,
            body="Внутренняя заметка менеджера",
            is_internal=True,
        )

        response = self.client.get(f"/messenger/c/{conversation.public_token}/")

        self.assertContains(response, "Ответ клиенту")
        self.assertNotContains(response, "Внутренняя заметка менеджера")

    def test_expired_public_conversation_capability_is_rejected(self):
        conversation = Conversation.objects.create(
            client_email="client@example.test",
            title="Диалог",
            public_access_expires_at=timezone.now() - timedelta(seconds=1),
        )

        response = self.client.get(f"/messenger/c/{conversation.public_token}/")
        api_response = self.client.post(
            "/messenger/api/messages/",
            data=json.dumps(
                {
                    "conversation_token": str(conversation.public_token),
                    "message": "Просроченная запись",
                    "personal_data_consent": "on",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 410)
        self.assertEqual(api_response.status_code, 403)
        self.assertFalse(conversation.messages.exists())

    def test_closed_public_conversation_rejects_new_messages(self):
        conversation = Conversation.objects.create(
            client_email="client@example.test",
            title="Диалог",
            status=Conversation.Status.CLOSED,
        )

        response = self.client.post(
            f"/messenger/c/{conversation.public_token}/",
            {"body": "Попытка открыть закрытый диалог"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(conversation.messages.exists())

    def test_message_api_rejects_cross_origin_browser_post(self):
        response = self.client.post(
            "/messenger/api/messages/",
            data=json.dumps(
                {
                    "message": "Спам с чужого сайта",
                    "title": "AurumWeb",
                    "personal_data_consent": "on",
                }
            ),
            content_type="application/json",
            HTTP_ORIGIN="https://evil.example",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Conversation.objects.exists())

    def test_message_api_rejects_invalid_token(self):
        response = self.client.post(
            "/messenger/api/messages/",
            data=json.dumps(
                {
                    "conversation_token": "not-a-uuid",
                    "message": "Здравствуйте",
                    "title": "Диалог с сайта",
                    "personal_data_consent": "on",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
