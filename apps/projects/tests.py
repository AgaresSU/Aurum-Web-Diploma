import tempfile
from datetime import timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Profile
from apps.billing.models import Invoice, ManagedSite, Order
from apps.integrations.models import OutboundTask
from apps.leads.models import Lead, QuickConsultation
from apps.leads.services import create_quick_consultation_invoice
from apps.messaging.models import Conversation

from .models import Project, ProjectEvent, ProjectFile
from .reminders import collect_deadline_reminders, enqueue_deadline_reminders
from .services import complete_project, delete_project_file


class ProjectLinkingTests(TestCase):
    def test_quick_consultation_onboarding_does_not_create_empty_project(self):
        consultation = QuickConsultation.objects.create(
            telegram_chat_id="onboarding",
            status=QuickConsultation.Status.WAITING_OFFER,
        )

        self.assertIsNone(consultation.project_id)

    def test_related_records_share_lead_project(self):
        user = User.objects.create_user("client", email="client@example.test", password="StrongPass123!")
        lead = Lead.objects.create(
            user=user,
            name="Клиент",
            email=user.email,
            subject="Сайт под ключ",
            service_type="Сайт",
            task="Нужен сайт.",
        )
        conversation = Conversation.objects.create(lead=lead, user=user, client_email=user.email, title=lead.subject)
        invoice = Invoice.objects.create(
            lead=lead,
            user=user,
            client_name=lead.name,
            client_email=user.email,
            title="Счет по сайту",
            amount="48000.00",
        )
        order = Order.objects.create(lead=lead, user=user, invoice=invoice, title=lead.subject)
        site = ManagedSite.objects.create(lead=lead, user=user, order=order, title=lead.subject)

        project_ids = {lead.project_id, conversation.project_id, invoice.project_id, order.project_id, site.project_id}

        self.assertEqual(len(project_ids), 1)

    @override_settings(AURUMWEB_OUTBOX_ENABLED=True)
    def test_quick_consultation_invoice_keeps_consultation_project(self):
        consultation = QuickConsultation.objects.create(
            telegram_chat_id="42",
            client_name="Артем",
            client_email="artem@example.test",
            question="Нужен Telegram-бот.",
            status=QuickConsultation.Status.NEW,
        )

        invoice, _payment_url = create_quick_consultation_invoice(consultation, "8000.00")

        consultation.refresh_from_db()
        self.assertEqual(invoice.project_id, consultation.project_id)


class ProjectFileAccessTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", email="owner@example.test", password="StrongPass123!")
        self.other = User.objects.create_user("other", email="other@example.test", password="StrongPass123!")
        self.lead = Lead.objects.create(
            user=self.owner,
            name="Владелец",
            email=self.owner.email,
            subject="Проект с файлами",
            task="Подготовить проект.",
        )

    def test_private_file_is_available_only_to_owner(self):
        storage = ProjectFile._meta.get_field("file").storage
        previous_location = storage._location
        with tempfile.TemporaryDirectory() as temp_dir:
            storage._location = Path(temp_dir)
            storage.__dict__.pop("base_location", None)
            storage.__dict__.pop("location", None)
            try:
                project_file = ProjectFile.objects.create(
                    project=self.lead.project,
                    uploaded_by=self.owner,
                    title="Техническое задание",
                    file=SimpleUploadedFile("brief.txt", b"project brief", content_type="text/plain"),
                    original_name="brief.txt",
                    size=13,
                )

                self.client.force_login(self.owner)
                owner_response = self.client.get(f"/projects/files/{project_file.public_id}/download/")
                self.assertEqual(owner_response.status_code, 200)
                self.assertEqual(b"".join(owner_response.streaming_content), b"project brief")

                self.client.force_login(self.other)
                other_response = self.client.get(f"/projects/files/{project_file.public_id}/download/")
                self.assertEqual(other_response.status_code, 404)
            finally:
                storage._location = previous_location
                storage.__dict__.pop("base_location", None)
                storage.__dict__.pop("location", None)


class ProjectManagementTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            "project-manager",
            email="manager@example.test",
            password="StrongPass123!",
            is_staff=True,
        )
        self.client_user = User.objects.create_user(
            "managed-client",
            email="managed-client@example.test",
            password="StrongPass123!",
        )
        Profile.objects.create(user=self.client_user, role=Profile.Role.CLIENT)
        self.project = Project.objects.create(
            user=self.client_user,
            manager=self.manager,
            title="Управляемый проект",
            client_name="Клиент",
            client_email=self.client_user.email,
            status=Project.Status.IN_PROGRESS,
        )

    def test_file_deletion_removes_storage_record_and_history_event(self):
        storage = ProjectFile._meta.get_field("file").storage
        previous_location = storage._location
        with tempfile.TemporaryDirectory() as temp_dir:
            storage._location = Path(temp_dir)
            storage.__dict__.pop("base_location", None)
            storage.__dict__.pop("location", None)
            try:
                project_file = ProjectFile.objects.create(
                    project=self.project,
                    uploaded_by=self.manager,
                    title="Макет",
                    file=SimpleUploadedFile("layout.txt", b"layout", content_type="text/plain"),
                    original_name="layout.txt",
                    size=6,
                )
                stored_name = project_file.file.name
                event = ProjectEvent.objects.create(
                    project=self.project,
                    project_file=project_file,
                    actor=self.manager,
                    kind=ProjectEvent.Kind.FILE,
                    title="Добавлен файл: Макет",
                )

                with self.captureOnCommitCallbacks(execute=True):
                    delete_project_file(project_file)

                self.assertFalse(ProjectFile.objects.filter(pk=project_file.pk).exists())
                self.assertFalse(ProjectEvent.objects.filter(pk=event.pk).exists())
                self.assertFalse(storage.exists(stored_name))
            finally:
                storage._location = previous_location
                storage.__dict__.pop("base_location", None)
                storage.__dict__.pop("location", None)

    def test_project_can_be_completed_without_client_notification(self):
        project, changed = complete_project(
            self.project,
            actor=self.manager,
            notify_client=False,
        )

        self.assertTrue(changed)
        self.assertEqual(project.status, Project.Status.COMPLETED)
        self.assertIsNotNone(project.completed_at)
        event = project.events.get(title="Проект завершен")
        self.assertFalse(event.client_visible)
        self.assertFalse(
            OutboundTask.objects.filter(task_type=OutboundTask.TaskType.PROJECT_COMPLETED_TELEGRAM).exists()
        )

    def test_project_completion_can_notify_client_once(self):
        project, changed = complete_project(
            self.project,
            actor=self.manager,
            notify_client=True,
        )
        _project, repeated = complete_project(
            self.project,
            actor=self.manager,
            notify_client=True,
        )

        self.assertTrue(changed)
        self.assertFalse(repeated)
        self.assertTrue(project.events.get(title="Проект завершен").client_visible)
        tasks = OutboundTask.objects.filter(task_type=OutboundTask.TaskType.PROJECT_COMPLETED_TELEGRAM)
        self.assertEqual(tasks.count(), 1)
        self.assertEqual(tasks.get().payload, {"project_id": project.pk})


class ProjectDeadlineReminderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "deadline-client",
            email="deadline@example.test",
            password="StrongPass123!",
        )
        Profile.objects.create(
            user=self.user,
            role=Profile.Role.CLIENT,
            telegram_chat_id="12345",
            telegram_notifications_enabled=True,
        )

    def test_only_seven_three_and_one_day_deadlines_are_collected(self):
        today = timezone.localdate()
        included = Project.objects.create(
            user=self.user,
            client_email=self.user.email,
            title="Проект со сроком",
            due_at=today + timedelta(days=7),
        )
        Project.objects.create(
            user=self.user,
            client_email=self.user.email,
            title="Проект без напоминания",
            due_at=today + timedelta(days=2),
        )

        reminders = collect_deadline_reminders(today=today)

        self.assertEqual([item["source_id"] for item in reminders], [included.pk])
        self.assertEqual(reminders[0]["days_left"], 7)

    @override_settings(
        AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED=True,
        AURUMWEB_CLIENT_EMAIL_NOTIFICATIONS_ENABLED=True,
    )
    def test_repeated_enqueue_is_idempotent_for_each_channel(self):
        today = timezone.localdate()
        Project.objects.create(
            user=self.user,
            client_email=self.user.email,
            title="Проект с уведомлениями",
            due_at=today + timedelta(days=3),
        )

        first = enqueue_deadline_reminders(today=today)
        second = enqueue_deadline_reminders(today=today)

        self.assertEqual(first, {"candidates": 1, "tasks": 2})
        self.assertEqual(second, {"candidates": 1, "tasks": 2})
        self.assertEqual(OutboundTask.objects.count(), 2)
        self.assertEqual(
            set(OutboundTask.objects.values_list("task_type", flat=True)),
            {
                OutboundTask.TaskType.DEADLINE_REMINDER_EMAIL,
                OutboundTask.TaskType.DEADLINE_REMINDER_TELEGRAM,
            },
        )
