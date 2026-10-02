from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Profile
from apps.accounts.totp import generate_totp_secret, totp_code
from apps.billing.models import Invoice, ManagedSite, Order
from apps.content.models import Service
from apps.integrations.models import OutboundTask, RobokassaSettings, TelegramBotSettings
from apps.leads.models import Lead
from apps.messaging.models import Conversation, Message
from apps.projects.models import Project, ProjectEvent, ProjectFile, ProjectStage
from apps.projects.services import record_project_event


class OfficeAccessTests(TestCase):
    def _enable_staff_totp(self, user):
        profile = user.profile
        secret = generate_totp_secret()
        profile.set_totp_secret(secret)
        profile.totp_enabled = True
        profile.save(update_fields=("totp_secret", "totp_enabled"))
        return secret

    def test_enrolled_totp_seed_is_not_redisplayed_and_page_is_not_cached(self):
        staff = User.objects.create_user(
            username="admin",
            email="admin@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        secret = self._enable_staff_totp(staff)
        self.client.force_login(staff)

        response = self.client.get("/office/security/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, secret)
        self.assertContains(response, "сохранен и больше не отображается")
        self.assertIn("no-cache", response["Cache-Control"])

    def test_totp_rotation_keeps_old_seed_until_new_seed_is_confirmed(self):
        staff = User.objects.create_user(
            username="admin",
            email="admin@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        old_secret = self._enable_staff_totp(staff)
        self.client.force_login(staff)

        response = self.client.post(
            "/office/security/",
            {"action": "start_totp_rotation", "password": "StrongPass123!", "code": totp_code(old_secret)},
        )

        self.assertRedirects(response, "/office/security/", fetch_redirect_response=False)
        new_secret = self.client.session["pending_totp_rotation_secret"]
        staff.profile.refresh_from_db()
        self.assertEqual(staff.profile.get_totp_secret(), old_secret)

        response = self.client.post(
            "/office/security/",
            {"action": "confirm_totp_rotation", "password": "StrongPass123!", "code": totp_code(new_secret)},
        )

        self.assertRedirects(response, "/office/security/", fetch_redirect_response=False)
        staff.profile.refresh_from_db()
        self.assertEqual(staff.profile.get_totp_secret(), new_secret)
        self.assertNotIn("pending_totp_rotation_secret", self.client.session)

    def test_office_requires_staff_access(self):
        response = self.client.get("/office/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_client_user_cannot_access_office(self):
        client_user = User.objects.create_user(
            username="client",
            email="client@example.test",
            password="StrongPass123!",
        )
        Profile.objects.create(user=client_user, role=Profile.Role.CLIENT)

        self.client.force_login(client_user)
        for path in ("/office/", "/office/leads/", "/office/integrations/", "/admin/"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302, path)
            self.assertTrue(
                response["Location"].startswith("/accounts/login/") or response["Location"].startswith("/admin/login/"),
                path,
            )

    def test_global_search_treats_sql_injection_payload_as_plain_text(self):
        staff = User.objects.create_user(
            username="search_admin",
            email="search-admin@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        project = Project.objects.create(title="Безопасный проект", client_email="client@example.test")
        self.client.force_login(staff)

        response = self.client.get(
            "/office/search/",
            {"q": "' OR 1=1; DROP TABLE projects_project; --"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["results"]["projects"].count(), 0)
        self.assertTrue(Project.objects.filter(pk=project.pk, title="Безопасный проект").exists())

    def test_client_preview_does_not_treat_staff_user_as_client(self):
        staff = User.objects.create_user(
            username="aurumweb_admin",
            email="admin@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        lead = Lead.objects.create(
            user=staff,
            name="Ирина, сервисная компания",
            email="client2@example.test",
            subject="Поддержка сайта",
            service_type="Сайт",
            task="Нужно сопровождение сайта.",
        )
        conversation = Conversation.objects.create(
            lead=lead,
            user=staff,
            client_email=lead.email,
            title=lead.subject,
        )

        self.client.force_login(staff)
        response = self.client.get(f"/office/conversations/{conversation.pk}/client-preview/")

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Ирина, сервисная компания", body)
        self.assertIn("client2@example.test", body)
        self.assertNotIn("<h2>aurumweb_admin</h2>", body)
        self.assertNotIn("<p><span>Имя</span><strong>aurumweb_admin</strong></p>", body)
        self.assertNotIn("<p><span>Email</span><strong>admin@example.test</strong></p>", body)
        self.assertNotIn("password", body.lower())
        self.assertNotIn("totp_secret", body.lower())

    def test_staff_can_create_site_card_from_order(self):
        staff = User.objects.create_user(
            username="admin",
            email="admin@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        lead = Lead.objects.create(
            name="Клиент",
            email="client@example.test",
            subject="Сайт под ключ",
            service_type="Сайт",
            task="Нужен сайт.",
        )
        order = Order.objects.create(
            lead=lead,
            title="Сайт под ключ",
            client_name="Клиент",
            client_email=lead.email,
            status=Order.Status.DISCOVERY,
        )

        self.client.force_login(staff)
        response = self.client.post(f"/office/orders/{order.pk}/site/")

        site = ManagedSite.objects.get(order=order)
        self.assertRedirects(response, f"/office/sites/{site.pk}/", fetch_redirect_response=False)
        self.assertEqual(site.title, order.title)
        self.assertEqual(site.lead, lead)

    def test_staff_can_close_lead_from_office(self):
        staff = User.objects.create_user(
            username="admin_close_lead",
            email="admin-close-lead@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        lead = Lead.objects.create(
            name="Тестовая заявка",
            email="test-lead@example.test",
            subject="Тест",
            service_type="Сайт",
            task="Тестовая открытая заявка.",
            status=Lead.Status.NEW,
        )

        self.client.force_login(staff)
        response = self.client.post(
            f"/office/leads/{lead.pk}/",
            data={"action": "close_lead", "next": "/office/leads/?status=new"},
        )

        self.assertRedirects(response, "/office/leads/?status=new", fetch_redirect_response=False)
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.LOST)

    def test_staff_can_close_conversation_from_office(self):
        staff = User.objects.create_user(
            username="admin_close_conversation",
            email="admin-close-conversation@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        conversation = Conversation.objects.create(
            title="Тестовый диалог",
            client_email="client-dialog@example.test",
            status=Conversation.Status.WAITING_MANAGER,
        )

        self.client.force_login(staff)
        response = self.client.post(
            f"/office/conversations/{conversation.pk}/",
            data={"action": "close_conversation", "next": "/office/conversations/?status=waiting_manager"},
        )

        self.assertRedirects(response, "/office/conversations/?status=waiting_manager", fetch_redirect_response=False)
        conversation.refresh_from_db()
        self.assertEqual(conversation.status, Conversation.Status.CLOSED)

    def test_project_attention_counts_projects_and_clears_after_view(self):
        staff = User.objects.create_user(
            username="admin_project_attention",
            email="admin-project-attention@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        client_user = User.objects.create_user(
            username="project_attention_client",
            email="project-attention-client@example.test",
            password="StrongPass123!",
        )
        project = Project.objects.create(
            user=client_user,
            title="Проект с обновлениями",
            status=Project.Status.IN_PROGRESS,
        )
        ProjectEvent.objects.create(
            project=project,
            actor=client_user,
            kind=ProjectEvent.Kind.FILE,
            title="Клиент добавил файл",
        )
        ProjectEvent.objects.create(
            project=project,
            actor=client_user,
            kind=ProjectEvent.Kind.APPROVAL,
            title="Клиент ответил по этапу",
        )
        record_project_event(
            project,
            "Изменение менеджера",
            actor=staff,
        )

        self.client.force_login(staff)
        response = self.client.get("/office/projects/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["office_unread_events"], 1)
        self.assertIsNotNone(project.events.get(title="Изменение менеджера").manager_seen_at)

        response = self.client.get(f"/office/projects/{project.public_id}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["office_unread_events"], 0)

    def test_conversation_attention_counts_dialogs_and_clears_after_view(self):
        staff = User.objects.create_user(
            username="admin_conversation_attention",
            email="admin-conversation-attention@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        conversation = Conversation.objects.create(
            title="Диалог с новыми сообщениями",
            client_email="conversation-attention@example.test",
            status=Conversation.Status.WAITING_MANAGER,
        )
        Message.objects.create(
            conversation=conversation,
            author_role=Message.AuthorRole.CLIENT,
            body="Первое сообщение",
        )
        Message.objects.create(
            conversation=conversation,
            author_role=Message.AuthorRole.CLIENT,
            body="Второе сообщение",
        )

        self.client.force_login(staff)
        response = self.client.get("/office/conversations/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["office_unread_messages"], 1)

        response = self.client.get(f"/office/conversations/{conversation.pk}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["office_unread_messages"], 0)

    def test_conversation_attention_clears_after_manager_reply(self):
        staff = User.objects.create_user(
            username="admin_conversation_reply",
            email="admin-conversation-reply@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        conversation = Conversation.objects.create(
            title="Диалог для ответа",
            client_email="conversation-reply@example.test",
            status=Conversation.Status.WAITING_MANAGER,
        )
        Message.objects.create(
            conversation=conversation,
            author_role=Message.AuthorRole.CLIENT,
            body="Жду ответа",
        )

        self.client.force_login(staff)
        response = self.client.post(
            f"/office/conversations/{conversation.pk}/",
            data={"action": "reply", "body": "Ответ менеджера"},
        )

        self.assertRedirects(
            response,
            f"/office/conversations/{conversation.pk}/",
            fetch_redirect_response=False,
        )
        conversation.refresh_from_db()
        self.assertEqual(conversation.status, Conversation.Status.WAITING_CLIENT)
        self.assertIsNotNone(conversation.manager_last_read_at)

        response = self.client.get("/office/conversations/")
        self.assertEqual(response.context["office_unread_messages"], 0)

    def test_staff_can_complete_order_from_office(self):
        staff = User.objects.create_user(
            username="admin_complete_order",
            email="admin-complete-order@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        order = Order.objects.create(
            title="Тестовый заказ",
            client_name="Клиент",
            client_email="client-order@example.test",
            status=Order.Status.IN_PROGRESS,
        )

        self.client.force_login(staff)
        response = self.client.post(
            f"/office/orders/{order.pk}/",
            data={"action": "complete_order", "next": "/office/orders/?status=in_progress"},
        )

        self.assertRedirects(response, "/office/orders/?status=in_progress", fetch_redirect_response=False)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)

    def test_staff_can_complete_project_and_notify_client(self):
        staff = User.objects.create_user(
            username="admin_complete_project",
            email="admin-complete-project@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        client_user = User.objects.create_user(
            username="project_client",
            email="project-client@example.test",
            password="StrongPass123!",
        )
        Profile.objects.create(user=client_user, role=Profile.Role.CLIENT)
        project = Project.objects.create(
            user=client_user,
            title="Проект клиента",
            client_email=client_user.email,
            status=Project.Status.IN_PROGRESS,
        )

        self.client.force_login(staff)
        response = self.client.post(
            f"/office/projects/{project.public_id}/",
            data={"action": "complete_project", "notify_client": "1"},
        )

        self.assertRedirects(
            response,
            f"/office/projects/{project.public_id}/",
            fetch_redirect_response=False,
        )
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.COMPLETED)
        self.assertIsNotNone(project.completed_at)
        self.assertTrue(project.events.get(title="Проект завершен").client_visible)
        self.assertTrue(
            OutboundTask.objects.filter(
                task_type=OutboundTask.TaskType.PROJECT_COMPLETED_TELEGRAM,
                payload={"project_id": project.pk},
            ).exists()
        )

    def test_staff_can_complete_selected_projects_and_notify_clients(self):
        staff = User.objects.create_user(
            username="admin_complete_projects",
            email="admin-complete-projects@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        projects = [
            Project.objects.create(title=f"Проект {index}", status=Project.Status.IN_PROGRESS) for index in range(1, 3)
        ]

        self.client.force_login(staff)
        response = self.client.post(
            "/office/projects/",
            data={
                "action": "complete_selected",
                "notify_client": "1",
                "project_ids": [str(project.public_id) for project in projects],
            },
        )

        self.assertRedirects(response, "/office/projects/", fetch_redirect_response=False)
        for project in projects:
            project.refresh_from_db()
            self.assertEqual(project.status, Project.Status.COMPLETED)
            self.assertTrue(project.events.get(title="Проект завершен").client_visible)
        self.assertEqual(
            OutboundTask.objects.filter(
                task_type=OutboundTask.TaskType.PROJECT_COMPLETED_TELEGRAM,
            ).count(),
            2,
        )

    def test_staff_can_complete_selected_projects_without_notifications(self):
        staff = User.objects.create_user(
            username="admin_complete_projects_silent",
            email="admin-complete-projects-silent@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        project = Project.objects.create(title="Тихое завершение", status=Project.Status.REVIEW)

        self.client.force_login(staff)
        response = self.client.post(
            "/office/projects/",
            data={
                "action": "complete_selected",
                "notify_client": "0",
                "project_ids": [str(project.public_id)],
            },
        )

        self.assertRedirects(response, "/office/projects/", fetch_redirect_response=False)
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.COMPLETED)
        self.assertFalse(project.events.get(title="Проект завершен").client_visible)
        self.assertFalse(
            OutboundTask.objects.filter(
                task_type=OutboundTask.TaskType.PROJECT_COMPLETED_TELEGRAM,
            ).exists()
        )

    def test_staff_can_open_or_create_dialogue_from_client_card(self):
        staff = User.objects.create_user(
            username="admin_client_dialogue",
            email="admin-client-dialogue@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        client_user = User.objects.create_user(
            username="dialogue_client",
            email="dialogue-client@example.test",
            password="StrongPass123!",
        )
        Profile.objects.create(user=client_user, role=Profile.Role.CLIENT)

        self.client.force_login(staff)
        response = self.client.post(
            f"/office/clients/{client_user.pk}/",
            data={"action": "open_conversation"},
        )

        conversation = Conversation.objects.get(user=client_user)
        self.assertRedirects(
            response,
            f"/office/conversations/{conversation.pk}/",
            fetch_redirect_response=False,
        )
        self.assertEqual(conversation.client_email, client_user.email)
        self.assertEqual(conversation.status, Conversation.Status.WAITING_MANAGER)

    def test_staff_can_delete_project_file_and_its_history_entry(self):
        staff = User.objects.create_user(
            username="admin_delete_project_file",
            email="admin-delete-file@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        project = Project.objects.create(title="Проект с файлом")
        project_file = ProjectFile.objects.create(
            project=project,
            uploaded_by=staff,
            title="Черновик",
            file="projects/test/draft.txt",
            original_name="draft.txt",
            size=10,
        )
        event = ProjectEvent.objects.create(
            project=project,
            project_file=project_file,
            actor=staff,
            kind=ProjectEvent.Kind.FILE,
            title="Добавлен файл: Черновик",
        )

        self.client.force_login(staff)
        response = self.client.post(
            f"/office/projects/{project.public_id}/",
            data={"action": "delete_file", "file_id": str(project_file.public_id)},
        )

        self.assertRedirects(
            response,
            f"/office/projects/{project.public_id}/",
            fetch_redirect_response=False,
        )
        self.assertFalse(ProjectFile.objects.filter(pk=project_file.pk).exists())
        self.assertFalse(ProjectEvent.objects.filter(pk=event.pk).exists())

    def test_staff_can_save_telegram_settings_with_masked_token(self):
        staff = User.objects.create_user(
            username="admin2",
            email="admin2@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)

        self.client.force_login(staff)
        response = self.client.post(
            "/office/integrations/",
            data={
                "action": "save_telegram",
                "password": "StrongPass123!",
                "is_enabled": "on",
                "new_bot_token": "123456:secret-token",
                "admin_chat_id": "42",
                "new_webhook_secret": "webhook-secret",
                "webhook_url": "https://aurumweb.test/integrations/telegram/webhook/",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        config = TelegramBotSettings.load()
        self.assertTrue(config.configured)
        self.assertEqual(config.get_bot_token(), "123456:secret-token")
        self.assertNotEqual(config.bot_token, "123456:secret-token")
        self.assertIn("************", body)
        self.assertNotIn("123456:secret-token", body)

    def test_staff_can_save_robokassa_settings_with_masked_passwords(self):
        staff = User.objects.create_user(
            username="admin_robokassa",
            email="admin-robokassa@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)

        self.client.force_login(staff)
        response = self.client.post(
            "/office/integrations/",
            data={
                "action": "save_robokassa",
                "password": "StrongPass123!",
                "is_enabled": "on",
                "merchant_login": "aurumweb",
                "new_password1": "password-one",
                "new_password2": "password-two",
                "hash_algorithm": "md5",
                "payment_url": "https://auth.robokassa.ru/Merchant/Index.aspx",
                "receipt_enabled": "on",
                "receipt_tax": "none",
                "receipt_payment_method": "full_payment",
                "receipt_payment_object": "service",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        config = RobokassaSettings.load()
        self.assertTrue(config.configured)
        self.assertEqual(config.get_password1(), "password-one")
        self.assertEqual(config.get_password2(), "password-two")
        self.assertNotEqual(config.password1, "password-one")
        self.assertNotEqual(config.password2, "password-two")
        self.assertIn("************", body)
        self.assertNotIn("password-one", body)
        self.assertNotIn("password-two", body)

    def test_staff_navigation_pages_open(self):
        staff = User.objects.create_user(
            username="admin3",
            email="admin3@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        lead = Lead.objects.create(
            name="Клиент",
            email="client@example.test",
            subject="Сайт под ключ",
            service_type="Сайт",
            task="Нужен сайт.",
        )
        conversation = Conversation.objects.create(
            lead=lead,
            client_email=lead.email,
            title=lead.subject,
        )
        invoice = Invoice.objects.create(
            lead=lead,
            title="Счет по сайту",
            client_name="Клиент",
            client_email=lead.email,
            amount="48000.00",
        )
        order = Order.objects.create(
            lead=lead,
            invoice=invoice,
            title="Сайт под ключ",
            client_name="Клиент",
            client_email=lead.email,
        )
        site = ManagedSite.objects.create(
            lead=lead,
            order=order,
            title="Сайт клиента",
        )

        self.client.force_login(staff)
        paths = (
            "/office/",
            "/office/inbox/",
            "/office/approvals/",
            "/office/calendar/",
            "/office/projects/",
            f"/office/projects/{lead.project.public_id}/",
            "/office/clients/",
            "/office/search/?q=сайт",
            "/office/revenue/",
            "/office/leads/",
            f"/office/leads/{lead.pk}/",
            "/office/conversations/",
            f"/office/conversations/{conversation.pk}/",
            f"/office/conversations/{conversation.pk}/client-preview/",
            "/office/orders/",
            f"/office/orders/{order.pk}/",
            "/office/sites/",
            f"/office/sites/{site.pk}/",
            "/office/invoices/",
            f"/office/invoices/{invoice.pk}/",
            "/office/services/",
            "/office/integrations/",
            "/office/security/",
        )
        for path in paths:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)

    def test_staff_calendar_combines_project_stage_invoice_and_site_deadlines(self):
        staff = User.objects.create_user(
            username="admin_calendar",
            email="admin-calendar@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        client = User.objects.create_user(
            username="calendar_client",
            email="calendar-client@example.test",
            password="StrongPass123!",
        )
        Profile.objects.create(user=client, role=Profile.Role.CLIENT)
        lead = Lead.objects.create(
            user=client,
            name="Клиент календаря",
            email=client.email,
            subject="Сайт к запуску",
            service_type="Сайт",
            task="Подготовить сайт к запуску.",
        )
        today = timezone.localdate()
        project = lead.project
        project.due_at = today
        project.save(update_fields=("due_at", "updated_at"))
        stage = project.stages.first()
        stage.title = "Согласование дизайна"
        stage.due_at = today
        stage.save(update_fields=("title", "due_at", "updated_at"))
        Invoice.objects.create(
            lead=lead,
            user=client,
            project=project,
            title="Счет перед запуском",
            client_name=lead.name,
            client_email=lead.email,
            amount="48000.00",
            due_date=today,
        )
        ManagedSite.objects.create(
            user=client,
            lead=lead,
            project=project,
            title="Рабочий сайт",
            domain_expires_at=today,
        )
        self.client.force_login(staff)

        response = self.client.get(f"/office/calendar/?month={today:%Y-%m}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Сайт к запуску")
        self.assertContains(response, "Согласование дизайна")
        self.assertContains(response, "Счет перед запуском")
        self.assertContains(response, "Рабочий сайт")
        self.assertContains(response, "Ближайшие 14 дней")
        self.assertTrue(response.context["upcoming_deadlines"])

    def test_staff_can_resend_stage_after_requested_changes(self):
        staff = User.objects.create_user(
            username="admin_approval",
            email="admin-approval@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        lead = Lead.objects.create(
            name="Клиент согласования",
            email="approval-client@example.test",
            subject="Сайт с согласованием",
            service_type="Сайт",
            task="Согласовать главную страницу.",
        )
        stage = lead.project.stages.first()
        stage.approval_required = True
        stage.approval_state = ProjectStage.ApprovalState.CHANGES_REQUESTED
        stage.approval_comment = "Нужно заменить фотографию."
        stage.status = ProjectStage.Status.IN_PROGRESS
        stage.save()
        self.client.force_login(staff)

        response = self.client.post("/office/approvals/", data={"stage_id": stage.pk})

        self.assertEqual(response.status_code, 302)
        stage.refresh_from_db()
        self.assertEqual(stage.approval_state, ProjectStage.ApprovalState.PENDING)
        self.assertEqual(stage.status, ProjectStage.Status.WAITING_CLIENT)
        self.assertEqual(stage.approval_comment, "")

    def test_staff_cannot_create_invoice_with_non_positive_amount(self):
        staff = User.objects.create_user(
            username="admin_invoice",
            email="admin-invoice@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        lead = Lead.objects.create(
            name="Клиент",
            email="client-invoice@example.test",
            subject="Сайт под ключ",
            service_type="Сайт",
            task="Нужен сайт.",
        )

        self.client.force_login(staff)
        for amount in ("0.00", "-1.00"):
            with self.subTest(amount=amount):
                response = self.client.post(
                    f"/office/leads/{lead.pk}/invoice/",
                    data={
                        "title": "Счет по сайту",
                        "description": "Работы по сайту.",
                        "amount": amount,
                        "due_date": "",
                        "item_name": "Сайт под ключ",
                        "item_note": "",
                    },
                )

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Сумма счета должна быть больше нуля.")
                self.assertFalse(Invoice.objects.exists())

    def test_office_layout_contains_account_and_logout(self):
        staff = User.objects.create_user(
            username="admin_layout",
            email="admin-layout@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)

        self.client.force_login(staff)
        response = self.client.get("/office/")

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("office-sidebar", body)
        self.assertIn("office-account", body)
        self.assertIn("admin_layout", body)
        self.assertIn('action="/accounts/logout/"', body)

    def test_staff_can_update_service_price_from_office(self):
        staff = User.objects.create_user(
            username="admin_price",
            email="admin-price@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        service = Service.objects.create(
            title="Консультации по сайтам и автоматизации",
            slug="technical-consulting-test",
            service_type=Service.ServiceType.CONSULTING,
            short_description="Разбор проекта и план работ.",
            price_amount=5000,
            price_unit="за консультацию",
            is_featured=True,
            is_published=True,
        )

        self.client.force_login(staff)
        response = self.client.post(
            f"/office/services/{service.pk}/",
            data={
                "price_prefix": "от",
                "price_amount": "6000",
                "price_unit": "за консультацию",
                "price_note": "после первичного описания задачи",
                "is_featured": "on",
                "is_published": "on",
                "sort_order": "15",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        service.refresh_from_db()
        self.assertEqual(service.price_amount, 6000)
        self.assertEqual(service.price_display, "от 6 000 ₽ за консультацию")
        self.assertContains(response, "от 6 000 ₽ за консультацию")

    def test_readiness_page_is_not_published(self):
        staff = User.objects.create_user(
            username="admin4",
            email="admin4@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        Profile.objects.create(user=staff, role=Profile.Role.ADMIN)
        self._enable_staff_totp(staff)
        self.client.force_login(staff)
        response = self.client.get("/office/readiness/")

        self.assertEqual(response.status_code, 404)
        office_response = self.client.get("/office/")
        self.assertNotContains(office_response, "/office/readiness/")
        self.assertNotContains(office_response, "проверки запуска")
