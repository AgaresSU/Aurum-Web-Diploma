from django.db import transaction
from django.utils import timezone

from .models import Project, ProjectEvent, ProjectFile, ProjectStage


def _project_values(instance):
    lead = getattr(instance, "lead", None)
    user = getattr(instance, "user", None) or getattr(lead, "user", None)
    title = (
        getattr(instance, "title", "")
        or getattr(instance, "subject", "")
        or getattr(lead, "subject", "")
        or "Проект AurumWeb"
    )
    email = (
        getattr(instance, "client_email", "")
        or getattr(instance, "email", "")
        or getattr(lead, "email", "")
        or getattr(user, "email", "")
    )
    name = (
        getattr(instance, "client_name", "")
        or getattr(instance, "name", "")
        or getattr(lead, "name", "")
        or (user.get_full_name() if user else "")
    )
    service_type = getattr(instance, "service_type", "") or getattr(lead, "service_type", "")
    return {
        "user": user,
        "title": title,
        "client_name": name,
        "client_email": email,
        "service_type": service_type,
    }


def _related_project(instance):
    lead = getattr(instance, "lead", None)
    if lead and getattr(lead, "project_id", None):
        return lead.project
    order = getattr(instance, "order", None)
    if order and getattr(order, "project_id", None):
        return order.project
    invoice = getattr(instance, "invoice", None)
    if invoice and getattr(invoice, "project_id", None):
        return invoice.project
    consultation = getattr(instance, "quick_consultation", None)
    if consultation and getattr(consultation, "project_id", None):
        return consultation.project
    return None


@transaction.atomic
def ensure_project_for_instance(instance):
    project = _related_project(instance)
    if project is None:
        values = _project_values(instance)
        project = Project.objects.create(**values)
        ProjectStage.objects.bulk_create(
            (
                ProjectStage(
                    project=project, title="Разбор задачи", status=ProjectStage.Status.IN_PROGRESS, sort_order=10
                ),
                ProjectStage(project=project, title="Согласование", sort_order=20),
                ProjectStage(project=project, title="Выполнение", sort_order=30),
                ProjectStage(project=project, title="Передача результата", sort_order=40),
            )
        )
        ProjectEvent.objects.create(project=project, title="Проект создан", description=project.title)
    type(instance).objects.filter(pk=instance.pk, project__isnull=True).update(project=project)
    instance.project_id = project.pk
    return project


def record_project_event(
    project,
    title,
    *,
    kind=ProjectEvent.Kind.STATUS,
    description="",
    actor=None,
    client_visible=True,
    project_file=None,
):
    manager_seen_at = None
    if actor is not None and (actor.is_staff or actor.is_superuser):
        manager_seen_at = timezone.now()
    return ProjectEvent.objects.create(
        project=project,
        project_file=project_file,
        actor=actor,
        kind=kind,
        title=title,
        description=description,
        client_visible=client_visible,
        manager_seen_at=manager_seen_at,
    )


@transaction.atomic
def delete_project_file(project_file):
    project_file = ProjectFile.objects.select_for_update().select_related("project").get(pk=project_file.pk)
    storage = project_file.file.storage
    stored_name = project_file.file.name

    ProjectEvent.objects.filter(
        project=project_file.project,
        project_file__isnull=True,
        kind=ProjectEvent.Kind.FILE,
        title=f"Добавлен файл: {project_file.title}",
    ).delete()
    project_file.delete()

    if stored_name:
        transaction.on_commit(lambda: storage.delete(stored_name))


@transaction.atomic
def complete_project(project, *, actor=None, notify_client=False):
    from apps.integrations.models import OutboundTask
    from apps.integrations.outbox import enqueue_outbound_task

    project = Project.objects.select_for_update().get(pk=project.pk)
    if project.status == Project.Status.COMPLETED:
        return project, False
    if project.status == Project.Status.CANCELLED:
        raise ValueError("Отмененный проект нельзя завершить.")

    project.status = Project.Status.COMPLETED
    project.completed_at = timezone.now()
    project.next_action = ""
    project.save(update_fields=("status", "completed_at", "next_action", "updated_at"))
    event = record_project_event(
        project,
        "Проект завершен",
        description="Работы по проекту завершены.",
        actor=actor,
        client_visible=notify_client,
    )

    if notify_client:
        enqueue_outbound_task(
            OutboundTask.TaskType.PROJECT_COMPLETED_TELEGRAM,
            {"project_id": project.pk},
            deduplication_key=f"project-completed-telegram:{project.pk}:{event.pk}",
        )

    return project, True


def update_project_status(project, status, *, title, description="", actor=None, next_action=None):
    if project is None:
        return None
    update_fields = []
    if project.status != status:
        project.status = status
        update_fields.append("status")
    if next_action is not None and project.next_action != next_action:
        project.next_action = next_action
        update_fields.append("next_action")
    if status == Project.Status.COMPLETED and project.completed_at is None:
        from django.utils import timezone

        project.completed_at = timezone.now()
        update_fields.append("completed_at")
    if update_fields:
        update_fields.append("updated_at")
        project.save(update_fields=tuple(update_fields))
        record_project_event(project, title, description=description, actor=actor)
    return project


@transaction.atomic
def request_stage_approval(stage, *, actor=None, force=False):
    stage = ProjectStage.objects.select_for_update().select_related("project").get(pk=stage.pk)
    already_pending = (
        stage.approval_state == ProjectStage.ApprovalState.PENDING
        and stage.status == ProjectStage.Status.WAITING_CLIENT
    )
    if already_pending and not force:
        return stage

    now = timezone.now()
    stage.approval_required = True
    stage.approval_state = ProjectStage.ApprovalState.PENDING
    stage.approval_requested_at = now
    stage.approval_decided_at = None
    stage.approval_comment = ""
    stage.approved_at = None
    stage.approved_by = None
    stage.completed_at = None
    stage.status = ProjectStage.Status.WAITING_CLIENT
    stage.save(
        update_fields=(
            "approval_required",
            "approval_state",
            "approval_requested_at",
            "approval_decided_at",
            "approval_comment",
            "approved_at",
            "approved_by",
            "completed_at",
            "status",
            "updated_at",
        )
    )

    project = stage.project
    project.status = Project.Status.WAITING_CLIENT
    project.next_action = f"Согласовать этап «{stage.title}»"
    project.save(update_fields=("status", "next_action", "updated_at"))
    record_project_event(
        project,
        f"Этап «{stage.title}» передан на согласование",
        kind=ProjectEvent.Kind.APPROVAL,
        actor=actor,
    )
    return stage


@transaction.atomic
def approve_project_stage(stage, *, actor, comment=""):
    stage = ProjectStage.objects.select_for_update().select_related("project").get(pk=stage.pk)
    if stage.approval_state != ProjectStage.ApprovalState.PENDING:
        raise ValueError("Этап уже обработан.")

    now = timezone.now()
    stage.status = ProjectStage.Status.COMPLETED
    stage.approval_state = ProjectStage.ApprovalState.APPROVED
    stage.approval_comment = comment.strip()
    stage.approval_decided_at = now
    stage.approved_at = now
    stage.approved_by = actor
    stage.completed_at = now
    stage.save(
        update_fields=(
            "status",
            "approval_state",
            "approval_comment",
            "approval_decided_at",
            "approved_at",
            "approved_by",
            "completed_at",
            "updated_at",
        )
    )

    project = stage.project
    project.status = Project.Status.IN_PROGRESS
    project.next_action = ""
    project.save(update_fields=("status", "next_action", "updated_at"))
    record_project_event(
        project,
        f"Согласован этап «{stage.title}»",
        kind=ProjectEvent.Kind.APPROVAL,
        description=stage.approval_comment,
        actor=actor,
    )
    return stage


@transaction.atomic
def request_project_stage_changes(stage, *, actor, comment):
    stage = ProjectStage.objects.select_for_update().select_related("project").get(pk=stage.pk)
    if stage.approval_state != ProjectStage.ApprovalState.PENDING:
        raise ValueError("Этап уже обработан.")

    clean_comment = comment.strip()
    if not clean_comment:
        raise ValueError("Опишите, что нужно изменить.")

    stage.status = ProjectStage.Status.IN_PROGRESS
    stage.approval_state = ProjectStage.ApprovalState.CHANGES_REQUESTED
    stage.approval_comment = clean_comment
    stage.approval_decided_at = timezone.now()
    stage.approved_at = None
    stage.approved_by = None
    stage.completed_at = None
    stage.save(
        update_fields=(
            "status",
            "approval_state",
            "approval_comment",
            "approval_decided_at",
            "approved_at",
            "approved_by",
            "completed_at",
            "updated_at",
        )
    )

    project = stage.project
    project.status = Project.Status.IN_PROGRESS
    project.next_action = f"Изменения по этапу «{stage.title}» приняты в работу"
    project.save(update_fields=("status", "next_action", "updated_at"))
    record_project_event(
        project,
        f"Запрошены изменения по этапу «{stage.title}»",
        kind=ProjectEvent.Kind.APPROVAL,
        description=clean_comment,
        actor=actor,
    )
    return stage
