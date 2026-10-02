import json
import tempfile
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.accounts.models import Profile
from apps.accounts.totp import generate_totp_secret
from apps.billing.models import Invoice, ManagedSite, Order
from apps.billing.services import create_robokassa_payment, issue_invoice, mark_payment_succeeded
from apps.content.models import Service, TemplateProduct, Testimonial
from apps.core.prod_readiness import _backup_readiness
from apps.integrations.models import TelegramBotSettings
from apps.leads.models import Lead
from apps.messaging.models import Conversation, Message


class LinkCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attrs_dict = dict(attrs)
        href = attrs_dict.get("href")
        if href:
            self.hrefs.append(href)


class VisibleTextCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.parts.append(data.strip())

    @property
    def text(self):
        return " ".join(" ".join(self.parts).split())


class ProductionReadinessTests(TestCase):
    def test_nested_backup_is_counted_as_fresh(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            backup_dir = Path(temp_dir)
            nested_backup = backup_dir / "manual-20260721" / "database.dump"
            nested_backup.parent.mkdir()
            nested_backup.touch()

            with patch.dict("os.environ", {"AURUMWEB_BACKUP_DIR": str(backup_dir)}):
                items = _backup_readiness()

        fresh_backup = next(item for item in items if item["title"] == "Свежий бэкап")
        self.assertEqual(fresh_backup["state"], "ok")
        self.assertIn("manual-20260721", fresh_backup["text"])


class ProductionSmokeWorkflowTests(TestCase):
    def _telegram_response(self, payload):
        class Response:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, exc_type, exc, traceback):
                return False

            def read(self_inner):
                return json.dumps(payload).encode("utf-8")

        return Response()

    def _enable_staff_totp(self, user):
        profile = user.profile
        profile.set_totp_secret(generate_totp_secret())
        profile.totp_enabled = True
        profile.save(update_fields=("totp_secret", "totp_enabled"))

    def _telegram_texts(self, urlopen):
        texts = []
        for call in urlopen.call_args_list:
            request_obj = call.args[0]
            if request_obj.data:
                payload = json.loads(request_obj.data.decode("utf-8"))
                texts.append(payload.get("text", ""))
        return "\n\n".join(texts)

    @override_settings(
        DEBUG=True,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_ADMIN_CHAT_ID="",
        AURUMWEB_SITE_URL="https://aurumweb.test",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    @patch("apps.integrations.telegram.request.urlopen")
    def test_client_office_billing_site_and_telegram_smoke_workflow(self, urlopen):
        urlopen.return_value = self._telegram_response({"ok": True, "result": {"message_id": 1}})
        config = TelegramBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123:test")
        config.admin_chat_id = "777"
        config.bot_username = "aurumweb_bot"
        config.save()

        staff = User.objects.create_user(
            username="admin",
            email="admin@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        client_user = User.objects.create_user(
            username="client",
            email="client@example.test",
            password="StrongPass123!",
        )
        client_profile = Profile.objects.create(user=client_user, role=Profile.Role.CLIENT)

        self.client.force_login(client_user)
        response = self.client.get("/client/profile/")
        client_profile.refresh_from_db()
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Подключить Telegram", body)
        self.assertIn('/client/telegram/connect/"', body)
        self.assertIn(f"/start {client_profile.telegram_link_code}", body)

        response = self.client.post(
            "/integrations/telegram/webhook/",
            data=json.dumps(
                {
                    "update_id": 1,
                    "message": {
                        "chat": {"id": 999},
                        "from": {"username": "client_tg"},
                        "text": f"/start {client_profile.telegram_link_code}",
                    },
                }
            ),
            content_type="application/json",
        )
        client_profile.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client_profile.telegram_chat_id, "999")
        self.assertTrue(client_profile.telegram_notifications_enabled)

        response = self.client.post(
            "/client/request/",
            data={
                "service_type": "Сайт под ключ",
                "subject": "Сайт под ключ и поддержка",
                "task": "Нужен сайт с заявками, личным кабинетом, счетами и Telegram-уведомлениями.",
            },
        )
        lead = Lead.objects.get(user=client_user)
        conversation = Conversation.objects.get(lead=lead)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(conversation.status, Conversation.Status.WAITING_MANAGER)

        self.client.force_login(staff)
        response = self.client.post(
            f"/office/conversations/{conversation.pk}/",
            data={"body": "Принял задачу. Подготовлю состав работ и счет."},
        )
        conversation.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(conversation.status, Conversation.Status.WAITING_CLIENT)
        self.assertTrue(conversation.messages.filter(author_role=Message.AuthorRole.MANAGER).exists())

        response = self.client.post(
            f"/office/leads/{lead.pk}/invoice/",
            data={
                "title": "Сайт под ключ и поддержка",
                "description": "Сборка сайта, ЛК, заявки, счета и поддержка после запуска.",
                "amount": "48000.00",
                "due_date": "",
            },
        )
        invoice = Invoice.objects.get(lead=lead)
        order = Order.objects.get(lead=lead)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(order.status, Order.Status.PROPOSAL)

        issue_invoice(invoice)
        payment = create_robokassa_payment(invoice)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)
        self.assertEqual(self.client.get(f"/billing/invoices/{invoice.public_token}/").status_code, 200)
        self.assertEqual(self.client.get(f"/billing/invoices/{invoice.public_token}/pay/").status_code, 200)

        mark_payment_succeeded(payment, {"OutSum": "48000.00"}, signature_valid=True)
        invoice.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(payment.status, payment.Status.SUCCEEDED)

        response = self.client.post(
            f"/office/orders/{order.pk}/",
            data={
                "status": Order.Status.IN_PROGRESS,
                "scope": order.scope,
                "internal_notes": "Smoke: заказ запущен.",
                "estimated_amount_min": "",
                "estimated_amount_max": "",
                "starts_at": "",
                "due_at": "",
            },
        )
        order.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(order.status, Order.Status.IN_PROGRESS)

        response = self.client.post(f"/office/orders/{order.pk}/site/")
        site = ManagedSite.objects.get(order=order)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(site.status, ManagedSite.Status.PLANNING)

        response = self.client.post(
            f"/office/sites/{site.pk}/",
            data={
                "title": site.title,
                "user": client_user.pk,
                "lead": lead.pk,
                "order": order.pk,
                "status": ManagedSite.Status.LIVE,
                "support_plan": ManagedSite.SupportPlan.BUSINESS,
                "url": "https://client.example.test",
                "domain_name": "client.example.test",
                "domain_registrar": "Регистратор",
                "domain_expires_at": "2027-05-17",
                "hosting_provider": "VPS",
                "hosting_plan": "Business",
                "hosting_expires_at": "2027-05-17",
                "ssl_provider": "Let's Encrypt",
                "ssl_expires_at": "2026-08-17",
                "ssl_auto_renew": "on",
                "last_backup_at": "",
                "backup_frequency": "Еженедельно",
                "last_health_check_at": "",
                "analytics_connected": "on",
                "seo_baseline_ready": "on",
                "support_until": "2026-08-17",
                "notes": "Smoke: сайт запущен и поставлен на поддержку.",
            },
        )
        site.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(site.status, ManagedSite.Status.LIVE)
        self.assertEqual(site.domain_name, "client.example.test")

        self.client.force_login(client_user)
        client_paths = (
            "/client/",
            f"/client/conversations/{conversation.public_token}/",
            f"/client/invoices/{invoice.public_token}/",
            f"/client/orders/{order.pk}/",
            f"/client/sites/{site.pk}/",
        )
        for path in client_paths:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)

        telegram_texts = self._telegram_texts(urlopen)
        for expected in (
            "Telegram подключен",
            "Новая заявка AurumWeb",
            "Заявка создана в AurumWeb",
            "Новое сообщение от менеджера AurumWeb",
            "Счет выставлен",
            "Выставлен счет AurumWeb",
            "Оплата получена",
            "Обновлен заказ AurumWeb",
            "Карточка сайта создана",
            "Обновлена карточка сайта",
        ):
            self.assertIn(expected, telegram_texts)


class PublicRouteTests(TestCase):
    def test_home_opens_public_site_and_retired_pages_redirect(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AurumWeb")
        self.assertContains(response, "От первой идеи до работающего результата")
        self.assertContains(response, "lux-hero-panel")
        self.assertContains(response, "lux-band--services")
        self.assertContains(response, "lux-split--workflow")
        self.assertNotContains(response, "Пример контроля проекта")
        self.assertNotContains(response, "lux-project-card")

        for path in ("/index.html", "/concepts/retired-variant.html"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)

    def test_reviews_replace_public_cases(self):
        Testimonial.objects.create(
            client_name="Алексей К.",
            client_details="Сайт услуг",
            project_title="Запуск сайта",
            text="Все этапы были понятны, а сайт запустили в согласованный срок.",
            is_published=True,
        )
        Testimonial.objects.create(
            client_name="Скрытый клиент",
            text="Этот отзыв пока не согласован.",
            is_published=False,
        )

        response = self.client.get("/content/reviews/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Отзывы")
        self.assertContains(response, "Алексей К.")
        self.assertNotContains(response, "Скрытый клиент")
        self.assertNotContains(response, 'href="/content/cases/"')

        legacy_response = self.client.get("/content/cases/")
        self.assertEqual(legacy_response.status_code, 301)
        self.assertEqual(legacy_response.url, "/content/reviews/")

        review_data = {
            "client_name": "Мария П.",
            "client_details": "Интернет-магазин",
            "project_title": "Обновление сайта",
            "text": "Работа прошла спокойно, все изменения объяснялись понятным языком.",
            "publication_consent": "on",
        }
        guest_response = self.client.post("/content/reviews/", review_data)
        self.assertEqual(guest_response.status_code, 302)
        self.assertIn("/accounts/login/", guest_response.url)

        client_user = User.objects.create_user(username="review_client", password="strong-password")
        self.client.force_login(client_user)
        review_without_consent = review_data.copy()
        review_without_consent.pop("publication_consent")
        consent_response = self.client.post("/content/reviews/", review_without_consent)
        self.assertEqual(consent_response.status_code, 200)
        self.assertContains(consent_response, "Подтвердите согласие на публикацию отзыва")
        self.assertFalse(Testimonial.objects.filter(submitted_by=client_user).exists())

        submit_response = self.client.post("/content/reviews/", review_data)

        self.assertRedirects(submit_response, "/content/reviews/")
        submitted_review = Testimonial.objects.get(submitted_by=client_user)
        self.assertFalse(submitted_review.is_published)
        self.assertIsNotNone(submitted_review.publication_consent_at)

        pending_page = self.client.get("/content/reviews/")
        self.assertNotContains(pending_page, review_data["text"])

        submitted_review.is_published = True
        submitted_review.save(update_fields=("is_published",))
        published_page = self.client.get("/content/reviews/")
        self.assertContains(published_page, review_data["text"])

    def test_public_core_pages_open(self):
        for path in (
            "/concepts/aurumweb.html",
            "/content/templates/",
            "/content/services/",
            "/content/reviews/",
            "/legal/privacy/",
            "/legal/offer/",
            "/accounts/login/",
            "/accounts/register/",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)

    def test_public_pages_do_not_show_internal_or_moderation_copy(self):
        public_paths = (
            "/concepts/aurumweb.html",
            "/content/services/",
            "/content/services/turnkey-websites/",
            "/content/services/website-care-support/",
            "/content/services/python-tools-automation/",
            "/content/services/telegram-bots/",
            "/content/services/technical-consulting/",
            "/legal/payment/",
            "/legal/privacy/",
            "/legal/offer/",
            "/legal/contacts/",
            "/accounts/login/",
        )
        blocked_phrases = (
            "Клиент попадает",
            "Административный доступ",
            "модерации платежного сервиса",
            "проверки модератором",
            "сверить с юристом",
            "Эти условия подготовлены",
            "Что важно знать",
            "Реквизиты и контакты продавца",
            "Снижает риск простоев",
            "Экономит время на рутине",
            "Ускоряет реакцию на обращения",
            "Помогает быстро понять объем работ",
        )

        for path in public_paths:
            response = self.client.get(path)
            body = response.content.decode()

            self.assertEqual(response.status_code, 200, path)
            for phrase in blocked_phrases:
                self.assertNotIn(phrase, body, path)

    def test_public_content_rephrases_technical_copy_for_clients(self):
        service = Service.objects.create(
            title="Тестовая услуга",
            slug="client-safe-service",
            service_type=Service.ServiceType.WEBSITE,
            short_description="Сайт с админкой, базовым SEO, SSL и интеграциями API.",
            full_description="Проект с CRM / админка, Webhook или polling и технической стабильностью.",
            business_value="Клиент получает понятную систему без технической суеты.",
            deliverables="Админка для контента\nИнтеграции с API\nКонтроль SSL и HTTPS\nSEO-доработки",
            is_featured=True,
        )
        TemplateProduct.objects.create(
            title="SEO-студия роста",
            slug="client-safe-template",
            template_type=TemplateProduct.TemplateType.WEBSITE,
            category="SEO и контент",
            industry="API-интеграции",
            short_description="Сайт для API, CRM, webhook и KPI без лишнего технического шума.",
            conversion_focus="Запрос КП и CRM-заявки.",
        )
        forbidden = (
            "админк",
            "API",
            "CRM",
            "SEO",
            "SSL",
            "HTTPS",
            "Webhook",
            "polling",
            "КП",
            "Django",
            "SQLite",
            "PostgreSQL",
            "Telegram Bot API",
            "техническ",
        )
        checks = (
            "/",
            "/content/services/",
            service.get_absolute_url(),
            "/content/templates/",
            "/content/reviews/",
        )

        for path in checks:
            response = self.client.get(path)
            parser = VisibleTextCollector()
            parser.feed(response.content.decode())
            body = parser.text

            self.assertEqual(response.status_code, 200, path)
            for phrase in forbidden:
                self.assertNotIn(phrase, body, path)

    def test_public_pages_have_basic_seo_meta(self):
        for path in (
            "/concepts/aurumweb.html",
            "/content/templates/",
            "/content/services/",
            "/content/reviews/",
            "/legal/payment/",
            "/brief/",
        ):
            response = self.client.get(path)
            body = response.content.decode()

            self.assertEqual(response.status_code, 200, path)
            self.assertIn('name="description"', body, path)
            self.assertIn('property="og:title"', body, path)
            self.assertIn('rel="canonical"', body, path)

    def test_public_pages_use_selected_arlux_layout(self):
        for path in (
            "/",
            "/content/templates/",
            "/content/services/",
            "/content/reviews/",
            "/legal/offer/",
            "/brief/",
            "/accounts/login/",
        ):
            response = self.client.get(path)
            body = response.content.decode()

            self.assertEqual(response.status_code, 200, path)
            self.assertIn("lux-nav", body, path)
            self.assertIn("lux-footer", body, path)
            self.assertIn("Запросить разбор", body, path)

    def test_public_pages_send_security_headers(self):
        response = self.client.get("/concepts/aurumweb.html")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertEqual(response["Cross-Origin-Opener-Policy"], "same-origin")
        self.assertIn("default-src 'self'", response["Content-Security-Policy-Report-Only"])
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy-Report-Only"])
        self.assertNotIn("'unsafe-inline'", response["Content-Security-Policy-Report-Only"])

    @override_settings(CSP_ENFORCE_ENABLED=True)
    def test_public_pages_can_enforce_content_security_policy(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("default-src 'self'", response["Content-Security-Policy"])
        self.assertIn("object-src 'none'", response["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
        self.assertIn("'nonce-", response["Content-Security-Policy"])
        self.assertNotIn("'unsafe-inline'", response["Content-Security-Policy"])
        nonce = response.wsgi_request.csp_nonce
        self.assertContains(response, f'nonce="{nonce}"')

    @override_settings(
        CSP_ENFORCE_ENABLED=True,
        AURUMWEB_YANDEX_METRIKA_ID="109930794",
    )
    def test_yandex_metrika_inline_loader_uses_request_nonce(self):
        response = self.client.get("/")

        nonce = response.wsgi_request.csp_nonce
        self.assertContains(response, f'<script nonce="{nonce}">')
        self.assertContains(response, "https://mc.yandex.ru/metrika/tag.js")
        self.assertNotContains(response, "style=")
        self.assertIn(f"'nonce-{nonce}'", response["Content-Security-Policy"])

    def test_public_navigation_links_do_not_point_to_private_admin(self):
        public_pages = ("/concepts/aurumweb.html", "/content/templates/", "/content/services/", "/brief/")
        checked = set()

        for page in public_pages:
            response = self.client.get(page)
            self.assertEqual(response.status_code, 200, page)

            parser = LinkCollector()
            parser.feed(response.content.decode())
            for href in parser.hrefs:
                parsed = urlsplit(href)
                if parsed.scheme and parsed.netloc and parsed.netloc != "testserver":
                    continue
                if parsed.path.startswith(("/admin/", "/office/")):
                    self.fail(f"Public page {page} links to private admin path: {href}")
                if not parsed.path.startswith("/"):
                    continue

                target = parsed.path
                if parsed.query:
                    target = f"{target}?{parsed.query}"
                if target in checked or target.startswith(("/messenger/c/", "/billing/invoices/")):
                    continue
                checked.add(target)

                target_response = self.client.get(target)
                self.assertNotIn(target_response.status_code, (404, 500), target)

    def test_public_navigation_does_not_show_admin_button_for_staff_session(self):
        staff = User.objects.create_user(username="admin", email="admin@example.com", password="pass", is_staff=True)
        self.client.force_login(staff)

        for path in ("/concepts/aurumweb.html", "/content/templates/", "/brief/"):
            response = self.client.get(path)
            body = response.content.decode()

            self.assertEqual(response.status_code, 200, path)
            self.assertNotIn(">Админка<", body, path)
            self.assertNotIn("Открыть админку", body, path)
            self.assertIn("Личный кабинет" if path != "/brief/" else "Кабинет", body, path)

    def test_raw_template_demo_is_isolated_from_external_services(self):
        TemplateProduct.objects.create(
            title="Сайт кофейни",
            slug="coffee-house-landing",
            template_type=TemplateProduct.TemplateType.WEBSITE,
            short_description="Демонстрационный сайт кофейни.",
        )

        response = self.client.get("/template-demos/coffee-house-landing/")
        body = response.content.decode()
        palette = self.client.get("/template-demos/coffee-house-landing/assets/aurum-palette.css")
        palette_body = palette.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("sandbox allow-scripts allow-same-origin", response["Content-Security-Policy"])
        self.assertIn("connect-src 'none'", response["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'self'", response["Content-Security-Policy"])
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertNotIn("mc.yandex.ru", body)
        self.assertNotIn("92884332", body)
        self.assertNotRegex(body, r'(?:src|data-original)=["\']https://[^"\']+\.tpl\.from\.biz')
        self.assertNotRegex(body, r'href=["\']https://(?:vk\.com|ok\.ru|t\.me|wa\.me)')
        self.assertIn("aurum-device-toolbar", body)
        self.assertNotRegex(body, r'<label[^>]+for="tablet_screen"[^>]+data-bs-toggle="tooltip"')
        self.assertEqual(palette.status_code, 200)
        self.assertNotIn(".aurum-device-toolbar label.active", palette_body)
        self.assertIn("#preview-container-mobile iframe", palette_body)
        self.assertNotIn("Создать сайт", body)

    def test_raw_template_shared_asset_keeps_original_url(self):
        TemplateProduct.objects.create(
            title="Сайт кофейни",
            slug="coffee-house-landing",
            template_type=TemplateProduct.TemplateType.WEBSITE,
            short_description="Демонстрационный сайт кофейни.",
        )
        asset_path = "mirror/from.biz/from-js-footer.min-e5844caf335c3e.js"

        response = self.client.get(f"/template-demos/coffee-house-landing/assets/{asset_path}")
        content = b"".join(response.streaming_content)

        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(content), 100_000)
        self.assertEqual(response["Cache-Control"], "public, max-age=3600")

    def test_token_pages_are_noindexed(self):
        invoice = Invoice.objects.create(
            title="Сайт под ключ",
            amount="48000.00",
            status=Invoice.Status.ISSUED,
        )
        conversation = Conversation.objects.create(title="Разбор проекта", client_email="client@example.com")

        for path in (f"/billing/invoices/{invoice.public_token}/", f"/messenger/c/{conversation.public_token}/"):
            response = self.client.get(path)
            body = response.content.decode()

            self.assertEqual(response.status_code, 200, path)
            self.assertIn("noindex", response["X-Robots-Tag"])
            self.assertIn('name="robots" content="noindex', body)

    def test_healthz_returns_database_status(self):
        response = self.client.get("/healthz/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "components": {"database": True, "cache": True}})

    def test_liveness_and_readiness_endpoints(self):
        live_response = self.client.get("/health/live/")
        ready_response = self.client.get("/health/ready/")

        self.assertEqual(live_response.status_code, 200)
        self.assertEqual(live_response.json(), {"ok": True, "service": "aurumweb"})
        self.assertEqual(ready_response.status_code, 200)
        self.assertTrue(ready_response.json()["ok"])

    @patch("apps.core.views.runtime_readiness")
    def test_readiness_returns_503_for_unavailable_dependency(self, readiness_mock):
        readiness_mock.return_value = {"ok": False, "components": {"database": False, "cache": True}}

        response = self.client.get("/health/ready/")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["ok"])

    def test_robots_points_to_sitemap_and_closes_private_sections(self):
        response = self.client.get("/robots.txt")

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Sitemap: http://testserver/sitemap.xml", body)
        self.assertIn("Disallow: /office/", body)
        self.assertIn("Disallow: /client/", body)
        self.assertIn("Disallow: /template-demos/", body)

    @override_settings(
        ALLOWED_HOSTS=["aurumweb.ru", "www.aurumweb.ru"],
        AURUMWEB_SITE_URL="https://www.aurumweb.ru",
        AURUMWEB_GOOGLE_SITE_VERIFICATION="google-verification-token",
        AURUMWEB_YANDEX_VERIFICATION="yandex-verification-token",
        AURUMWEB_YANDEX_METRIKA_ID="109930794",
    )
    def test_public_seo_urls_use_configured_primary_domain(self):
        response = self.client.get("/", HTTP_HOST="aurumweb.ru")
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('<link rel="canonical" href="https://www.aurumweb.ru/">', body)
        self.assertIn('<meta property="og:url" content="https://www.aurumweb.ru/">', body)
        self.assertIn(
            '<meta property="og:image" content="https://www.aurumweb.ru/assets/brand/aurumweb-mark-squircle-new.png">',
            body,
        )
        self.assertIn('type="application/ld+json"', body)
        self.assertIn('"@type": "ProfessionalService"', body)
        self.assertIn('"@type": "WebSite"', body)
        self.assertIn('<meta name="google-site-verification" content="google-verification-token">', body)
        self.assertIn('<meta name="yandex-verification" content="yandex-verification-token">', body)
        self.assertIn("https://mc.yandex.ru/metrika/tag.js?id=109930794", body)
        self.assertIn("ym(109930794, 'init'", body)

        robots = self.client.get("/robots.txt", HTTP_HOST="aurumweb.ru").content.decode()
        self.assertIn("Sitemap: https://www.aurumweb.ru/sitemap.xml", robots)

        sitemap = self.client.get("/sitemap.xml", HTTP_HOST="aurumweb.ru").content.decode()
        self.assertIn("<loc>https://www.aurumweb.ru/</loc>", sitemap)
        self.assertIn("<loc>https://www.aurumweb.ru/content/templates/</loc>", sitemap)
        self.assertNotIn("<loc>https://aurumweb.ru/", sitemap)

    @override_settings(
        ALLOWED_HOSTS=["aurumweb.ru", "www.aurumweb.ru"],
        PREPEND_WWW=True,
        SECURE_SSL_REDIRECT=False,
    )
    def test_bare_domain_redirects_to_www_when_enabled(self):
        response = self.client.get("/", HTTP_HOST="aurumweb.ru")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "http://www.aurumweb.ru/")

    def test_sitemap_includes_public_pages_and_published_services(self):
        Service.objects.create(
            title="Сайты под ключ",
            slug="websites",
            service_type=Service.ServiceType.WEBSITE,
            short_description="Создание сайтов.",
        )

        response = self.client.get("/sitemap.xml")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("application/xml"))
        body = response.content.decode()
        self.assertIn("http://testserver/content/templates/", body)
        self.assertIn("http://testserver/content/services/websites/", body)
        self.assertIn("http://testserver/legal/privacy/", body)
        self.assertNotIn("/office/", body)

    def test_legal_pages_cover_payment_data_and_contacts(self):
        Service.objects.create(
            title="Консультации по сайтам",
            slug="consulting",
            service_type=Service.ServiceType.CONSULTING,
            short_description="Разбор задачи.",
            price_amount=5000,
            price_unit="за консультацию",
        )
        checks = {
            "/legal/payment/": (
                "платежной странице",
                "платежного сервиса",
                "банковской картой",
                "СБП",
                "QR-код",
                "Консультации по сайтам",
                "от 5 000 ₽",
            ),
            "/legal/privacy/": ("заявки", "счета", "переписка"),
            "/legal/offer/": (
                "Публичная оферта",
                "Акцепт",
                "Скачать публичную оферту",
                "oferta_341902923482.docx",
                "Услуги и стартовые цены",
                "Консультации по сайтам",
                "от 5 000 ₽",
            ),
            "/legal/contacts/": ("Оставьте заявку", "диалог", "Email для связи"),
        }

        for path, expected_parts in checks.items():
            response = self.client.get(path)
            body = response.content.decode()

            self.assertEqual(response.status_code, 200, path)
            for part in expected_parts:
                self.assertIn(part, body, path)

    def test_legal_pages_are_rendered_as_documents(self):
        checks = {
            "/legal/offer/": ("официальная редакция", "Публичная оферта", "Акцепт"),
            "/legal/payment/": ("официальная редакция", "Условия оплаты", "Чек и документы"),
            "/legal/refund/": ("официальная редакция", "Частичный возврат", "Срок рассмотрения"),
            "/legal/privacy/": ("официальная редакция", "Политика обработки данных", "Отзыв согласия"),
            "/legal/contacts/": ("официальная редакция", "Контакты", "Реквизиты"),
        }

        for path, expected_parts in checks.items():
            response = self.client.get(path)
            body = response.content.decode()

            self.assertEqual(response.status_code, 200, path)
            self.assertIn("legal-clause", body, path)
            self.assertNotIn("Ниже —", body, path)
            self.assertNotIn("Другие юридические разделы", body, path)
            self.assertNotIn("Краткое пояснение", body, path)
            self.assertNotIn("Перед полным текстом", body, path)
            for part in expected_parts:
                self.assertIn(part, body, path)

    @override_settings(
        AURUMWEB_SELLER_NAME="Иванов Иван Иванович",
        AURUMWEB_SELLER_STATUS="Самозанятый исполнитель",
        AURUMWEB_SELLER_INN="123456789012",
        AURUMWEB_PUBLIC_EMAIL="aurumweb@aurumweb.ru",
        AURUMWEB_PUBLIC_PHONE="+7 900 000-00-00",
    )
    def test_legal_contacts_show_seller_requisites_from_settings(self):
        response = self.client.get("/legal/contacts/")
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Иванов Иван Иванович", body)
        self.assertIn("123456789012", body)
        self.assertIn("aurumweb@aurumweb.ru", body)
        self.assertIn("+7 900 000-00-00", body)

    @override_settings(
        AURUMWEB_SELLER_NAME="Иванов Иван Иванович",
        AURUMWEB_SELLER_STATUS="Самозанятый исполнитель",
        AURUMWEB_SELLER_INN="123456789012",
        AURUMWEB_PUBLIC_EMAIL="aurumweb@aurumweb.ru",
        AURUMWEB_PUBLIC_PHONE="+7 900 000-00-00",
    )
    def test_public_footer_shows_seller_requisites(self):
        response = self.client.get("/concepts/aurumweb.html")
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Иванов Иван Иванович", body)
        self.assertIn("ИНН 123456789012", body)
        self.assertIn("+7 900 000-00-00", body)

    def test_template_demo_assets_work_without_debug_static_routes(self):
        TemplateProduct.objects.create(
            title="Сайт кофейни",
            slug="coffee-house-landing",
            template_type=TemplateProduct.TemplateType.WEBSITE,
            category="Еда и гостеприимство",
            industry="Кофейни и рестораны",
            short_description="Демонстрационный сайт кофейни.",
        )

        response = self.client.get("/template-demos/coffee-house-landing/assets/aurum-palette.css")

        self.assertEqual(response.status_code, 200)
        self.assertIn("noindex", response["X-Robots-Tag"])
        self.assertTrue(response["Content-Type"].startswith("text/css"))

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
    def test_custom_404_renders_for_production_mode(self):
        response = self.client.get("/missing-page/")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Такой страницы нет", status_code=404)


class ProductionAuditCommandTests(TestCase):
    def _enable_staff_totp(self, user):
        profile = user.profile
        profile.set_totp_secret(generate_totp_secret())
        profile.totp_enabled = True
        profile.save(update_fields=("totp_secret", "totp_enabled"))

    @override_settings(
        DEBUG=False,
        SECRET_KEY="prod-secret-for-test-only-change-on-server",
        ALLOWED_HOSTS=["aurumweb.test"],
        CSRF_TRUSTED_ORIGINS=["https://aurumweb.test"],
        AURUMWEB_SITE_URL="https://aurumweb.test",
        AURUMWEB_SELLER_NAME="Иванов Иван Иванович",
        AURUMWEB_SELLER_STATUS="Самозанятый исполнитель",
        AURUMWEB_SELLER_INN="123456789012",
        AURUMWEB_PUBLIC_EMAIL="no-reply@aurumweb.test",
        AURUMWEB_PUBLIC_PHONE="+7 900 000-00-00",
        SESSION_COOKIE_SECURE=True,
        CSRF_COOKIE_SECURE=True,
        TELEGRAM_WEBHOOK_SECRET="settings-webhook-secret",
        TELEGRAM_WEBHOOK_URL="https://aurumweb.test/integrations/telegram/webhook/",
    )
    def test_prod_audit_json_hides_secret_values(self):
        staff = User.objects.create_user(
            username="audit-admin",
            email="audit-admin@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        config = TelegramBotSettings.load()
        config.is_enabled = True
        config.set_bot_token("123456:secret-token")
        config.admin_chat_id = "42"
        config.bot_username = "aurumweb_bot"
        config.set_webhook_secret("db-webhook-secret")
        config.save()

        output = StringIO()
        call_command("prod_audit", "--json", stdout=output)
        payload = json.loads(output.getvalue())

        self.assertIn("summary", payload)
        self.assertIn("groups", payload)
        self.assertEqual(payload["summary"]["blockers"], 0)
        receipt_items = [
            item for group in payload["groups"] for item in group["items"] if item["title"] == "Чеки для оплаты"
        ]
        self.assertEqual(receipt_items[0]["state"], "ok")
        self.assertNotIn("123456:secret-token", output.getvalue())
        self.assertNotIn("db-webhook-secret", output.getvalue())
        self.assertNotIn("settings-webhook-secret", output.getvalue())

    @override_settings(DEBUG=False, SECRET_KEY="dev-only-change-me", ALLOWED_HOSTS=[])
    def test_prod_audit_fails_on_blockers(self):
        with self.assertRaises(CommandError):
            call_command("prod_audit", stdout=StringIO())
