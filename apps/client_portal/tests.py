from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.accounts.models import Profile
from apps.billing.models import Invoice, ManagedSite, Order
from apps.integrations.models import TelegramBotSettings
from apps.leads.models import Lead
from apps.messaging.models import Conversation, Message
from apps.projects.models import ProjectEvent, ProjectFile, ProjectStage


class ClientPortalWorkflowTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            username="client",
            email="client@example.test",
            password="StrongPass123!",
        )
        Profile.objects.create(user=self.client_user, role=Profile.Role.CLIENT)
        self.other_user = User.objects.create_user(
            username="other",
            email="other@example.test",
            password="StrongPass123!",
        )
        Profile.objects.create(user=self.other_user, role=Profile.Role.CLIENT)
        self.lead = Lead.objects.create(
            user=self.client_user,
            name="Client",
            email=self.client_user.email,
            subject="Сайт под ключ",
            service_type="Сайт под ключ",
            task="Нужен сайт с заявками.",
        )
        self.conversation = Conversation.objects.create(
            lead=self.lead,
            user=self.client_user,
            client_email=self.client_user.email,
            title=self.lead.subject,
        )
        self.invoice = Invoice.objects.create(
            lead=self.lead,
            user=self.client_user,
            client_name="Client",
            client_email=self.client_user.email,
            title="Счет по сайту",
            amount="48000.00",
        )
        self.order = Order.objects.create(
            lead=self.lead,
            user=self.client_user,
            invoice=self.invoice,
            title="Сайт под ключ",
            client_name="Client",
            client_email=self.client_user.email,
            status=Order.Status.IN_PROGRESS,
            scope="Главная, услуги, заявки и кабинет клиента.",
        )
        self.site = ManagedSite.objects.create(
            user=self.client_user,
            lead=self.lead,
            order=self.order,
            title="Сайт клиента",
            status=ManagedSite.Status.DEVELOPMENT,
        )

    def test_dashboard_shows_project_status_and_orders(self):
        self.client.force_login(self.client_user)
        response = self.client.get("/client/")

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("текущий проект", body)
        self.assertIn("Сайт под ключ", body)
        self.assertIn("Этапы текущего проекта", body)
        self.assertIn("Оплаты", body)

    def test_dashboard_layout_keeps_summary_focused(self):
        self.client.force_login(self.client_user)
        response = self.client.get("/client/")

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("client-sidebar", body)
        self.assertIn("client-account", body)
        self.assertIn("client-dashboard-grid", body)
        self.assertIn("<strong>client</strong>", body)
        self.assertIn('action="/accounts/logout/"', body)
        self.assertNotIn("<h2>Мои сайты</h2>", body)
        self.assertNotIn("<h2>Реквизиты</h2>", body)
        self.assertIn("client-nav__primary", body)
        self.assertIn("Новая задача", body)
        self.assertNotIn("client-sidebar-telegram", body)

    def test_client_can_open_project_workspace(self):
        self.client.force_login(self.client_user)

        response = self.client.get(f"/client/projects/{self.lead.project.public_id}/")

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Обзор", body)
        self.assertIn("Этапы", body)
        self.assertIn("Материалы", body)
        self.assertIn("Сообщения", body)
        self.assertIn("Оплаты", body)
        self.assertIn("Сайт клиента", body)

    @override_settings(AURUMWEB_PROJECT_UPLOAD_MAX_FILES=1)
    def test_project_upload_quota_blocks_additional_files(self):
        project = self.lead.project
        ProjectFile.objects.create(
            project=project,
            title="Первый файл",
            file="projects/test/first.txt",
            original_name="first.txt",
            size=10,
        )
        self.client.force_login(self.client_user)

        response = self.client.post(
            f"/client/projects/{project.public_id}/",
            {
                "action": "upload_file",
                "title": "Второй файл",
                "file": SimpleUploadedFile("second.txt", b"second"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Достигнут лимит файлов проекта")
        self.assertEqual(project.files.count(), 1)

    def test_client_cannot_open_foreign_project(self):
        foreign_lead = Lead.objects.create(
            user=self.other_user,
            name="Other",
            email=self.other_user.email,
            subject="Чужой проект",
            task="Чужая задача.",
        )
        self.client.force_login(self.client_user)

        response = self.client.get(f"/client/projects/{foreign_lead.project.public_id}/")

        self.assertEqual(response.status_code, 404)

    def test_client_logout_button_ends_session(self):
        self.client.force_login(self.client_user)

        response = self.client.post("/accounts/logout/")
        self.assertIn(response.status_code, (200, 302))

        response = self.client.get("/client/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_profile_shows_telegram_deep_link(self):
        config = TelegramBotSettings.load()
        config.bot_username = "aurumweb_bot"
        config.save(update_fields=("bot_username", "updated_at"))

        self.client.force_login(self.client_user)
        response = self.client.get("/client/profile/")

        self.assertEqual(response.status_code, 200)
        self.client_user.profile.refresh_from_db()
        body = response.content.decode()
        self.assertIn("Подключить Telegram", body)
        self.assertIn('/client/telegram/connect/"', body)
        self.assertIn(f"/start {self.client_user.profile.telegram_link_code}", body)

    def test_telegram_connect_redirects_to_bot_with_profile_code(self):
        config = TelegramBotSettings.load()
        config.bot_username = "aurumweb_bot"
        config.save(update_fields=("bot_username", "updated_at"))

        self.client.force_login(self.client_user)
        response = self.client.get("/client/telegram/connect/")

        self.assertEqual(response.status_code, 302)
        self.client_user.profile.refresh_from_db()
        self.assertEqual(
            response["Location"],
            f"https://t.me/aurumweb_bot?start={self.client_user.profile.telegram_link_code}",
        )

    def test_client_can_open_own_order(self):
        self.client.force_login(self.client_user)
        response = self.client.get(f"/client/orders/{self.order.pk}/")

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Главная, услуги, заявки", body)
        self.assertIn("Открыть счет", body)

    def test_paid_invoice_detail_shows_paid_state_without_payment_cta(self):
        self.invoice.status = Invoice.Status.PAID
        self.invoice.save(update_fields=("status", "updated_at"))
        self.client.force_login(self.client_user)

        response = self.client.get(f"/client/invoices/{self.invoice.public_token}/")

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Счет оплачен", body)
        self.assertNotIn("Перейти к оплате счета", body)

    def test_client_cannot_open_foreign_order(self):
        foreign_order = Order.objects.create(
            user=self.other_user,
            title="Чужой заказ",
            client_email=self.other_user.email,
        )
        self.client.force_login(self.client_user)
        response = self.client.get(f"/client/orders/{foreign_order.pk}/")

        self.assertEqual(response.status_code, 404)

    def test_client_navigation_pages_open_for_owner(self):
        Message.objects.create(
            conversation=self.conversation,
            author=self.client_user,
            author_role=Message.AuthorRole.CLIENT,
            body="Нужен сайт с заявками.",
        )
        self.client.force_login(self.client_user)

        paths = (
            "/client/",
            "/client/approvals/",
            "/client/projects/",
            f"/client/projects/{self.lead.project.public_id}/",
            "/client/profile/",
            "/client/request/",
            "/client/conversations/",
            f"/client/conversations/{self.conversation.public_token}/",
            "/client/orders/",
            f"/client/orders/{self.order.pk}/",
            "/client/invoices/",
            f"/client/invoices/{self.invoice.public_token}/",
            "/client/sites/",
            f"/client/sites/{self.site.pk}/",
        )
        for path in paths:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)

    def test_client_conversation_does_not_render_internal_message(self):
        Message.objects.create(
            conversation=self.conversation,
            author_role=Message.AuthorRole.MANAGER,
            body="Обычный ответ менеджера",
        )
        Message.objects.create(
            conversation=self.conversation,
            author_role=Message.AuthorRole.MANAGER,
            body="Служебная заметка",
            is_internal=True,
        )
        self.client.force_login(self.client_user)

        response = self.client.get(f"/client/conversations/{self.conversation.public_token}/")

        self.assertContains(response, "Обычный ответ менеджера")
        self.assertNotContains(response, "Служебная заметка")

    def test_pending_approval_is_the_primary_dashboard_action(self):
        stage = self.lead.project.stages.first()
        stage.approval_required = True
        stage.approval_state = ProjectStage.ApprovalState.PENDING
        stage.status = ProjectStage.Status.WAITING_CLIENT
        stage.save()
        self.invoice.status = Invoice.Status.ISSUED
        self.invoice.save(update_fields=("status", "updated_at"))
        self.client.force_login(self.client_user)

        response = self.client.get("/client/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Согласуйте этап")
        self.assertContains(response, "/client/approvals/")

    def test_client_can_approve_pending_stage(self):
        stage = self.lead.project.stages.first()
        stage.approval_required = True
        stage.approval_state = ProjectStage.ApprovalState.PENDING
        stage.status = ProjectStage.Status.WAITING_CLIENT
        stage.save()
        self.client.force_login(self.client_user)

        response = self.client.post(
            "/client/approvals/",
            data={"action": "approve_stage", "stage_id": stage.pk, "comment": "Все принято."},
        )

        self.assertEqual(response.status_code, 302)
        stage.refresh_from_db()
        self.assertEqual(stage.approval_state, ProjectStage.ApprovalState.APPROVED)
        self.assertEqual(stage.status, ProjectStage.Status.COMPLETED)
        self.assertTrue(
            ProjectEvent.objects.filter(
                project=stage.project,
                kind=ProjectEvent.Kind.APPROVAL,
                title=f"Согласован этап «{stage.title}»",
            ).exists()
        )

    def test_pending_approval_shows_stage_result(self):
        stage = self.lead.project.stages.first()
        stage.approval_required = True
        stage.approval_state = ProjectStage.ApprovalState.PENDING
        stage.status = ProjectStage.Status.WAITING_CLIENT
        stage.result_summary = "Главная страница собрана и проверена на мобильных устройствах."
        stage.result_url = "https://example.test/result/"
        stage.save()
        self.client.force_login(self.client_user)

        response = self.client.get("/client/approvals/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, stage.result_summary)
        self.assertContains(response, stage.result_url)

    def test_client_can_return_pending_stage_with_comment(self):
        stage = self.lead.project.stages.first()
        stage.approval_required = True
        stage.approval_state = ProjectStage.ApprovalState.PENDING
        stage.status = ProjectStage.Status.WAITING_CLIENT
        stage.save()
        self.client.force_login(self.client_user)

        response = self.client.post(
            "/client/approvals/",
            data={
                "action": "request_stage_changes",
                "stage_id": stage.pk,
                "comment": "Поменяйте заголовок на главной странице.",
            },
        )

        self.assertEqual(response.status_code, 302)
        stage.refresh_from_db()
        self.assertEqual(stage.approval_state, ProjectStage.ApprovalState.CHANGES_REQUESTED)
        self.assertEqual(stage.approval_comment, "Поменяйте заголовок на главной странице.")
        self.assertEqual(stage.status, ProjectStage.Status.IN_PROGRESS)

    def test_client_cannot_open_foreign_private_objects(self):
        foreign_lead = Lead.objects.create(
            user=self.other_user,
            name="Other",
            email=self.other_user.email,
            subject="Чужой сайт",
            service_type="Сайт",
            task="Чужая задача.",
        )
        foreign_conversation = Conversation.objects.create(
            lead=foreign_lead,
            user=self.other_user,
            client_email=self.other_user.email,
            title=foreign_lead.subject,
        )
        foreign_invoice = Invoice.objects.create(
            lead=foreign_lead,
            user=self.other_user,
            client_name="Other",
            client_email=self.other_user.email,
            title="Чужой счет",
            amount="1000.00",
        )
        foreign_site = ManagedSite.objects.create(
            user=self.other_user,
            lead=foreign_lead,
            title="Чужой сайт",
        )

        self.client.force_login(self.client_user)
        paths = (
            f"/client/conversations/{foreign_conversation.public_token}/",
            f"/client/invoices/{foreign_invoice.public_token}/",
            f"/client/sites/{foreign_site.pk}/",
        )
        for path in paths:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404, path)
