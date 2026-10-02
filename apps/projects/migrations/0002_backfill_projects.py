from django.db import migrations
from django.db.models import Q


DEFAULT_STAGES = (
    ("Разбор задачи", "in_progress", 10),
    ("Согласование", "pending", 20),
    ("Выполнение", "pending", 30),
    ("Передача результата", "pending", 40),
)


def _create_project(Project, ProjectStage, ProjectEvent, source, *, title=None, email=None, name=None, user_id=None):
    project = Project.objects.create(
        user_id=user_id or getattr(source, "user_id", None),
        title=title or getattr(source, "title", "") or getattr(source, "subject", "") or "Проект AurumWeb",
        service_type=getattr(source, "service_type", ""),
        client_name=name or getattr(source, "client_name", "") or getattr(source, "name", ""),
        client_email=email or getattr(source, "client_email", "") or getattr(source, "email", ""),
    )
    ProjectStage.objects.bulk_create(
        [
            ProjectStage(project_id=project.pk, title=stage_title, status=status, sort_order=sort_order)
            for stage_title, status, sort_order in DEFAULT_STAGES
        ]
    )
    ProjectEvent.objects.create(project_id=project.pk, title="Проект создан")
    return project


def forwards(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    ProjectStage = apps.get_model("projects", "ProjectStage")
    ProjectEvent = apps.get_model("projects", "ProjectEvent")
    Lead = apps.get_model("leads", "Lead")
    QuickConsultation = apps.get_model("leads", "QuickConsultation")
    Conversation = apps.get_model("messaging", "Conversation")
    Invoice = apps.get_model("billing", "Invoice")
    Order = apps.get_model("billing", "Order")
    ManagedSite = apps.get_model("billing", "ManagedSite")

    for lead in Lead.objects.filter(project__isnull=True).iterator():
        project = _create_project(Project, ProjectStage, ProjectEvent, lead)
        Lead.objects.filter(pk=lead.pk).update(project_id=project.pk)
        Conversation.objects.filter(lead_id=lead.pk, project__isnull=True).update(project_id=project.pk)
        Invoice.objects.filter(lead_id=lead.pk, project__isnull=True).update(project_id=project.pk)
        Order.objects.filter(lead_id=lead.pk, project__isnull=True).update(project_id=project.pk)
        ManagedSite.objects.filter(lead_id=lead.pk, project__isnull=True).update(project_id=project.pk)

    for order in Order.objects.filter(project__isnull=True).iterator():
        project = None
        if order.invoice_id:
            invoice = Invoice.objects.filter(pk=order.invoice_id).first()
            if invoice and invoice.project_id:
                project = Project.objects.get(pk=invoice.project_id)
        if project is None:
            project = _create_project(Project, ProjectStage, ProjectEvent, order)
        Order.objects.filter(pk=order.pk).update(project_id=project.pk)
        if order.invoice_id:
            Invoice.objects.filter(pk=order.invoice_id, project__isnull=True).update(project_id=project.pk)
        ManagedSite.objects.filter(order_id=order.pk, project__isnull=True).update(project_id=project.pk)

    for invoice in Invoice.objects.filter(project__isnull=True).iterator():
        order = Order.objects.filter(invoice_id=invoice.pk, project__isnull=False).first()
        project = Project.objects.get(pk=order.project_id) if order else None
        if project is None:
            project = _create_project(Project, ProjectStage, ProjectEvent, invoice)
        Invoice.objects.filter(pk=invoice.pk).update(project_id=project.pk)

    for conversation in Conversation.objects.filter(project__isnull=True).iterator():
        project = _create_project(Project, ProjectStage, ProjectEvent, conversation)
        Conversation.objects.filter(pk=conversation.pk).update(project_id=project.pk)

    for site in ManagedSite.objects.filter(project__isnull=True).iterator():
        project = _create_project(Project, ProjectStage, ProjectEvent, site)
        ManagedSite.objects.filter(pk=site.pk).update(project_id=project.pk)

    meaningful_consultations = QuickConsultation.objects.filter(project__isnull=True).filter(
        Q(invoice__isnull=False) | Q(status__in=("new", "in_discussion", "invoiced", "paid"))
    )
    for consultation in meaningful_consultations.iterator():
        project = None
        if consultation.invoice_id:
            invoice = Invoice.objects.filter(pk=consultation.invoice_id).first()
            if invoice and invoice.project_id:
                project = Project.objects.get(pk=invoice.project_id)
        if project is None:
            project = _create_project(
                Project,
                ProjectStage,
                ProjectEvent,
                consultation,
                title=f"Быстрая консультация #{consultation.pk}",
                email=consultation.client_email,
                name=consultation.client_name or consultation.first_name,
            )
        QuickConsultation.objects.filter(pk=consultation.pk).update(project_id=project.pk)


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0009_invoice_project_managedsite_project_order_project"),
        ("leads", "0007_lead_project_quickconsultation_project"),
        ("messaging", "0003_conversation_client_last_read_at_and_more"),
        ("projects", "0001_initial"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
