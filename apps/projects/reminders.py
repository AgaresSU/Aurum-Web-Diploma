from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import Invoice, ManagedSite
from apps.integrations.models import OutboundTask
from apps.integrations.outbox import enqueue_outbound_task

from .models import Project, ProjectStage

REMINDER_DAYS = (7, 3, 1)


def _profile_accepts_telegram(user):
    if not user:
        return False
    profile = getattr(user, "profile", None)
    return bool(profile and profile.telegram_chat_id and profile.telegram_notifications_enabled)


def _reminder(*, source, field, deadline, title, user, email, path):
    return {
        "source": source._meta.label_lower,
        "source_id": source.pk,
        "field": field,
        "deadline": deadline,
        "title": title,
        "user": user,
        "email": (email or "").strip(),
        "path": path,
    }


def collect_deadline_reminders(today=None):
    today = today or timezone.localdate()
    dates = {today + timedelta(days=days): days for days in REMINDER_DAYS}
    reminders = []

    projects = (
        Project.objects.select_related("user")
        .filter(due_at__in=dates)
        .exclude(status__in=(Project.Status.COMPLETED, Project.Status.CANCELLED))
    )
    for project in projects:
        reminders.append(
            _reminder(
                source=project,
                field="due_at",
                deadline=project.due_at,
                title=f"Срок проекта «{project.title}»",
                user=project.user,
                email=project.client_email or (project.user.email if project.user else ""),
                path=reverse("client_portal:project-detail", args=(project.public_id,)),
            )
        )

    stages = (
        ProjectStage.objects.select_related("project", "project__user")
        .filter(due_at__in=dates)
        .exclude(status=ProjectStage.Status.COMPLETED)
        .exclude(project__status__in=(Project.Status.COMPLETED, Project.Status.CANCELLED))
    )
    for stage in stages:
        project = stage.project
        reminders.append(
            _reminder(
                source=stage,
                field="due_at",
                deadline=stage.due_at,
                title=f"Этап «{stage.title}» проекта «{project.title}»",
                user=project.user,
                email=project.client_email or (project.user.email if project.user else ""),
                path=reverse("client_portal:project-detail", args=(project.public_id,)) + "#stages",
            )
        )

    invoices = Invoice.objects.select_related("user").filter(
        due_date__in=dates,
        status=Invoice.Status.ISSUED,
    )
    for invoice in invoices:
        reminders.append(
            _reminder(
                source=invoice,
                field="due_date",
                deadline=invoice.due_date,
                title=f"Срок оплаты счета «{invoice.title}»",
                user=invoice.user,
                email=invoice.client_email or (invoice.user.email if invoice.user else ""),
                path=reverse("billing:invoice-detail", args=(invoice.public_token,)),
            )
        )

    sites = ManagedSite.objects.select_related("user", "order").exclude(status=ManagedSite.Status.PAUSED)
    for site in sites:
        email = site.user.email if site.user else (site.order.client_email if site.order else "")
        path = reverse("client_portal:site-detail", args=(site.pk,))
        for field, label in (
            ("domain_expires_at", "Истекает регистрация домена"),
            ("hosting_expires_at", "Заканчивается размещение сайта"),
            ("ssl_expires_at", "Истекает сертификат безопасности"),
            ("support_until", "Заканчивается период поддержки"),
        ):
            deadline = getattr(site, field)
            if deadline in dates:
                reminders.append(
                    _reminder(
                        source=site,
                        field=field,
                        deadline=deadline,
                        title=f"{label}: {site.title}",
                        user=site.user,
                        email=email,
                        path=path,
                    )
                )

    for reminder in reminders:
        reminder["days_left"] = dates[reminder["deadline"]]
    return sorted(reminders, key=lambda item: (item["deadline"], item["title"]))


def _payload(reminder):
    return {
        "source": reminder["source"],
        "source_id": reminder["source_id"],
        "field": reminder["field"],
        "title": reminder["title"],
        "deadline": reminder["deadline"].isoformat(),
        "deadline_display": reminder["deadline"].strftime("%d.%m.%Y"),
        "days_left": reminder["days_left"],
        "path": reminder["path"],
        "user_id": reminder["user"].pk if reminder["user"] else None,
        "email": reminder["email"],
    }


def _deduplication_key(reminder, channel):
    return ":".join(
        (
            "deadline",
            reminder["source"],
            str(reminder["source_id"]),
            reminder["field"],
            reminder["deadline"].isoformat(),
            channel,
        )
    )


def enqueue_deadline_reminders(today=None):
    reminders = collect_deadline_reminders(today=today)
    tasks = []
    email_enabled = bool(
        settings.AURUMWEB_EMAIL_NOTIFICATIONS_ENABLED and settings.AURUMWEB_CLIENT_EMAIL_NOTIFICATIONS_ENABLED
    )
    for reminder in reminders:
        payload = _payload(reminder)
        if email_enabled and reminder["email"]:
            tasks.append(
                enqueue_outbound_task(
                    OutboundTask.TaskType.DEADLINE_REMINDER_EMAIL,
                    payload,
                    _deduplication_key(reminder, "email"),
                )
            )
        if _profile_accepts_telegram(reminder["user"]):
            tasks.append(
                enqueue_outbound_task(
                    OutboundTask.TaskType.DEADLINE_REMINDER_TELEGRAM,
                    payload,
                    _deduplication_key(reminder, "telegram"),
                )
            )
    return {"candidates": len(reminders), "tasks": len({task.pk for task in tasks})}
