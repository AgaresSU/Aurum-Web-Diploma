import json
from calendar import Calendar
from datetime import datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.accounts.access import needs_totp_setup
from apps.accounts.security import backup_code_stats, replace_backup_codes
from apps.accounts.totp import generate_totp_secret
from apps.billing.models import Invoice, ManagedSite, Order, Payment
from apps.billing.services import client_requisites_from_lead
from apps.content.models import Service
from apps.integrations.models import (
    IntegrationEvent,
    QuickConsultationBotSettings,
    RobokassaSettings,
    TelegramBotSettings,
)
from apps.integrations.quick_telegram_handlers import process_pending_quick_telegram_updates
from apps.integrations.robokassa import RobokassaClient
from apps.integrations.telegram import QuickConsultationBotClient, TelegramBotClient
from apps.integrations.telegram_handlers import process_pending_telegram_updates
from apps.leads.models import Lead, QuickConsultation
from apps.messaging.models import Conversation
from apps.projects.forms import ProjectFileUploadForm, ProjectStageForm, ProjectUpdateForm
from apps.projects.models import Project, ProjectEvent, ProjectFile, ProjectStage
from apps.projects.services import complete_project, delete_project_file, record_project_event, request_stage_approval

from .forms import (
    InvoiceCreateForm,
    LeadQualificationForm,
    LeadUpdateForm,
    ManagedSiteUpdateForm,
    ManagerMessageForm,
    OrderUpdateForm,
    QuickConsultationForm,
    QuickConsultationTelegramForm,
    RobokassaIntegrationForm,
    SecurityPasswordForm,
    ServicePriceForm,
    TelegramIntegrationForm,
    TotpSetupConfirmForm,
)
from .services import (
    add_manager_reply,
    close_conversation,
    close_lead,
    complete_order,
    create_invoice_draft_for_lead,
    create_managed_site_from_order,
    disconnect_client_telegram,
    invoice_quick_consultation,
    managed_site_update_snapshot,
    refresh_client_telegram_code,
    save_lead_qualification,
    save_managed_site_with_notifications,
    save_order_with_notifications,
    send_client_telegram_test,
    send_quick_consultation_message,
    store_proposal_draft,
)
from .view_support import (
    CHECKLIST_FIELDS,
    base_staff_member_required,
    build_otpauth_uri,
    conversation_email,
    conversation_user,
    dashboard_action,
    find_profile,
    get_profile,
    lead_metadata,
    qualification_initial,
    staff_member_required,
)


def _redirect_after_action(request, fallback, *args, **kwargs):
    next_url = request.POST.get("next", "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect(fallback, *args, **kwargs)


MONTH_NAMES = (
    "",
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)


def _selected_month(value):
    try:
        return datetime.strptime(value, "%Y-%m").date().replace(day=1)
    except (TypeError, ValueError):
        return timezone.localdate().replace(day=1)


def _deadline_events(start_date=None, end_date=None):
    items = []

    def date_filters(field):
        filters = {}
        if start_date:
            filters[f"{field}__gte"] = start_date
        if end_date:
            filters[f"{field}__lt"] = end_date
        return filters

    def add_event(day, *, title, label, url, kind):
        if day:
            items.append({"date": day, "title": title, "label": label, "url": url, "kind": kind})

    projects = Project.objects.filter(**date_filters("due_at")).exclude(
        status__in=(Project.Status.COMPLETED, Project.Status.CANCELLED)
    )
    for project in projects:
        add_event(
            project.due_at,
            title=project.title,
            label="Срок проекта",
            url=reverse("office:project-detail", args=(project.public_id,)),
            kind="project",
        )

    stages = (
        ProjectStage.objects.select_related("project")
        .filter(**date_filters("due_at"))
        .exclude(status=ProjectStage.Status.COMPLETED)
    )
    for stage in stages:
        add_event(
            stage.due_at,
            title=f"{stage.project.title}: {stage.title}",
            label="Этап",
            url=f"{reverse('office:project-detail', args=(stage.project.public_id,))}#project-stages",
            kind="stage",
        )

    invoices = Invoice.objects.filter(**date_filters("due_date")).exclude(
        status__in=(Invoice.Status.PAID, Invoice.Status.CANCELLED)
    )
    for invoice in invoices:
        add_event(
            invoice.due_date,
            title=invoice.title,
            label="Счет",
            url=reverse("office:invoice-detail", args=(invoice.pk,)),
            kind="invoice",
        )

    site_dates = Q()
    for field in ("domain_expires_at", "hosting_expires_at", "ssl_expires_at", "support_until"):
        site_dates |= Q(**date_filters(field))
    sites = ManagedSite.objects.filter(site_dates)
    for site in sites:
        site_url = reverse("office:site-detail", args=(site.pk,))
        for field, label in (
            ("domain_expires_at", "Домен"),
            ("hosting_expires_at", "Размещение"),
            ("ssl_expires_at", "Сертификат"),
            ("support_until", "Поддержка"),
        ):
            day = getattr(site, field)
            if day and (start_date is None or day >= start_date) and (end_date is None or day < end_date):
                add_event(day, title=site.title, label=label, url=site_url, kind="site")

    return sorted(items, key=lambda item: (item["date"], item["label"], item["title"]))


def _deadline_calendar(month_start):
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    events = {}
    for event in _deadline_events(month_start, next_month):
        events.setdefault(event["date"], []).append(event)

    today = timezone.localdate()
    weeks = []
    for week in Calendar(firstweekday=0).monthdatescalendar(month_start.year, month_start.month):
        weeks.append(
            [
                {
                    "date": day,
                    "in_month": day.month == month_start.month,
                    "is_today": day == today,
                    "events": events.get(day, ()),
                }
                for day in week
            ]
        )
    return weeks, next_month


@staff_member_required
def dashboard(request):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    active_statuses = (
        Project.Status.DISCOVERY,
        Project.Status.WAITING_PAYMENT,
        Project.Status.IN_PROGRESS,
        Project.Status.WAITING_CLIENT,
        Project.Status.REVIEW,
        Project.Status.SUPPORT,
    )
    waiting_manager = Conversation.objects.filter(status=Conversation.Status.WAITING_MANAGER).order_by("-updated_at")
    overdue_projects = Project.objects.filter(due_at__lt=today).exclude(
        status__in=(Project.Status.COMPLETED, Project.Status.CANCELLED)
    )
    pending_approvals = ProjectStage.objects.filter(
        approval_required=True,
        approval_state=ProjectStage.ApprovalState.PENDING,
    )
    deadline_limit = today + timedelta(days=14)
    upcoming_deadlines = (
        Project.objects.filter(due_at__range=(today, deadline_limit))
        .exclude(status__in=(Project.Status.COMPLETED, Project.Status.CANCELLED))
        .count()
        + ProjectStage.objects.filter(due_at__range=(today, deadline_limit))
        .exclude(status=ProjectStage.Status.COMPLETED)
        .count()
        + Invoice.objects.filter(due_date__range=(today, deadline_limit), status=Invoice.Status.ISSUED).count()
    )
    draft_invoices = Invoice.objects.filter(status=Invoice.Status.DRAFT).order_by("-updated_at")
    action_sites = ManagedSite.objects.filter(
        Q(status=ManagedSite.Status.ACTION_REQUIRED)
        | Q(domain_expires_at__lte=today + timedelta(days=30))
        | Q(hosting_expires_at__lte=today + timedelta(days=30))
    ).order_by("domain_expires_at", "hosting_expires_at", "-updated_at")
    open_inbox = (
        Lead.objects.exclude(status=Lead.Status.LOST).count()
        + QuickConsultation.objects.exclude(
            status__in=(
                QuickConsultation.Status.CLOSED,
                QuickConsultation.Status.CANCELLED,
                QuickConsultation.Status.PAID,
            )
        ).count()
    )
    stats = {
        "active_projects": Project.objects.filter(status__in=active_statuses).count(),
        "open_inbox": open_inbox,
        "waiting_manager": waiting_manager.count(),
        "month_revenue": Payment.objects.filter(
            status=Payment.Status.SUCCEEDED, paid_at__date__gte=month_start
        ).aggregate(total=Sum("amount"))["total"]
        or 0,
    }
    next_actions = [
        dashboard_action(
            "Разобрать входящие",
            "Новые обращения с сайта и из быстрого бота.",
            "/office/inbox/",
            "Открыть входящие",
            stats["open_inbox"],
        ),
        dashboard_action(
            "Ответить клиентам",
            "Диалоги, где клиент ждет реакции менеджера.",
            "/office/conversations/?status=waiting_manager",
            "Открыть диалоги",
            waiting_manager.count(),
        ),
        dashboard_action(
            "Проверить согласования",
            "Этапы, по которым ожидается решение клиента.",
            "/office/approvals/",
            "Открыть согласования",
            pending_approvals.count(),
        ),
        dashboard_action(
            "Проверить сроки",
            "Просроченные и ближайшие даты по проектам и счетам.",
            "/office/calendar/",
            "Открыть календарь",
            overdue_projects.count() + upcoming_deadlines,
        ),
        dashboard_action(
            "Проверить сайты",
            "Сайты с риском по домену или ручным статусом внимания.",
            "/office/sites/?status=action_required",
            "Открыть сайты",
            action_sites.count(),
        ),
    ]
    return render(
        request,
        "office/dashboard.html",
        {
            "stats": stats,
            "next_actions": next_actions,
            "recent_projects": Project.objects.select_related("user", "manager")[:8],
            "draft_invoices": draft_invoices[:5],
        },
    )


@staff_member_required
def inbox(request):
    status = request.GET.get("status", "open")
    leads_query = Lead.objects.select_related("user", "project")
    consultations_query = QuickConsultation.objects.select_related("invoice", "project")
    if status == "open":
        leads_query = leads_query.exclude(status=Lead.Status.LOST)
        consultations_query = consultations_query.exclude(
            status__in=(QuickConsultation.Status.CLOSED, QuickConsultation.Status.CANCELLED)
        )
    elif status == "closed":
        leads_query = leads_query.filter(status=Lead.Status.LOST)
        consultations_query = consultations_query.filter(
            status__in=(QuickConsultation.Status.CLOSED, QuickConsultation.Status.CANCELLED)
        )

    entries = [
        {"kind": "lead", "item": lead, "updated_at": lead.updated_at}
        for lead in leads_query.order_by("-updated_at")[:80]
    ]
    entries.extend(
        {"kind": "quick", "item": consultation, "updated_at": consultation.updated_at}
        for consultation in consultations_query.order_by("-updated_at")[:80]
    )
    entries.sort(key=lambda entry: entry["updated_at"], reverse=True)
    return render(request, "office/inbox.html", {"entries": entries[:100], "status": status})


@staff_member_required
def projects(request):
    status = request.GET.get("status", "")
    query = request.GET.get("q", "").strip()
    if request.method == "POST" and request.POST.get("action") == "complete_selected":
        selected_tokens = []
        for value in request.POST.getlist("project_ids"):
            try:
                selected_tokens.append(UUID(value))
            except (TypeError, ValueError, AttributeError):
                continue

        if not selected_tokens:
            messages.warning(request, "Выберите хотя бы один проект.")
            return _redirect_after_action(request, "office:projects")

        selected_projects = (
            Project.objects.filter(public_id__in=selected_tokens)
            .exclude(status__in=(Project.Status.COMPLETED, Project.Status.CANCELLED))
            .order_by("pk")
        )
        notify_client = request.POST.get("notify_client") == "1"
        completed_count = 0
        with transaction.atomic():
            for project in selected_projects:
                _project, changed = complete_project(
                    project,
                    actor=request.user,
                    notify_client=notify_client,
                )
                completed_count += int(changed)

        if completed_count == 0:
            messages.info(request, "Выбранные проекты уже завершены или отменены.")
        elif notify_client:
            messages.success(
                request,
                f"Завершено проектов: {completed_count}. Клиенты увидят изменение в личном кабинете, "
                "а подключенным пользователям будет отправлено уведомление в Telegram.",
            )
        else:
            messages.success(request, f"Завершено проектов без уведомления клиентов: {completed_count}.")
        return _redirect_after_action(request, "office:projects")

    items = Project.objects.select_related("user", "manager").prefetch_related("stages")
    if status:
        items = items.filter(status=status)
    if query:
        items = items.filter(
            Q(title__icontains=query)
            | Q(client_name__icontains=query)
            | Q(client_email__icontains=query)
            | Q(service_type__icontains=query)
        )
    return render(
        request,
        "office/projects.html",
        {"projects": items[:100], "statuses": Project.Status.choices, "selected_status": status, "query": query},
    )


@staff_member_required
def project_detail(request, token):
    project = get_object_or_404(
        Project.objects.select_related("user", "manager").prefetch_related(
            "stages",
            "stages__result_files",
            "events",
            "files",
            "leads",
            "quick_consultations",
            "orders",
            "invoices",
            "sites",
        ),
        public_id=token,
    )
    project_form = ProjectUpdateForm(instance=project)
    edit_stage = None
    edit_stage_id = request.GET.get("stage", "").strip()
    if edit_stage_id:
        edit_stage = get_object_or_404(ProjectStage, pk=edit_stage_id, project=project)
    stage_form = ProjectStageForm(instance=edit_stage)
    file_form = ProjectFileUploadForm(project=project)

    if request.method == "POST":
        action = request.POST.get("action", "project")
        if action == "project":
            previous_status = project.status
            project_form = ProjectUpdateForm(request.POST, instance=project)
            if project_form.is_valid():
                project = project_form.save()
                if previous_status != project.status:
                    record_project_event(
                        project,
                        f"Статус изменен: {project.get_status_display()}",
                        actor=request.user,
                    )
                messages.success(request, "Проект обновлен.")
                return redirect("office:project-detail", token=project.public_id)
        elif action == "stage":
            stage = None
            stage_id = request.POST.get("stage_id")
            if stage_id:
                stage = get_object_or_404(ProjectStage, pk=stage_id, project=project)
                edit_stage = stage
            stage_form = ProjectStageForm(request.POST, instance=stage)
            if stage_form.is_valid():
                stage = stage_form.save(commit=False)
                stage.project = project
                if stage.status == ProjectStage.Status.COMPLETED and stage.completed_at is None:
                    stage.completed_at = timezone.now()
                stage.save()
                if stage.approval_required and stage.status == ProjectStage.Status.WAITING_CLIENT:
                    request_stage_approval(
                        stage,
                        actor=request.user,
                        force=stage.approval_state == ProjectStage.ApprovalState.CHANGES_REQUESTED,
                    )
                else:
                    if not stage.approval_required and stage.approval_state != ProjectStage.ApprovalState.NOT_REQUIRED:
                        stage.approval_state = ProjectStage.ApprovalState.NOT_REQUIRED
                        stage.approval_requested_at = None
                        stage.approval_decided_at = None
                        stage.approval_comment = ""
                        stage.save(
                            update_fields=(
                                "approval_state",
                                "approval_requested_at",
                                "approval_decided_at",
                                "approval_comment",
                                "updated_at",
                            )
                        )
                    record_project_event(
                        project,
                        f"Этап обновлен: {stage.title}",
                        description=stage.get_status_display(),
                        actor=request.user,
                    )
                messages.success(request, "Этап сохранен.")
                return redirect("office:project-detail", token=project.public_id)
        elif action == "file":
            file_form = ProjectFileUploadForm(request.POST, request.FILES, project=project)
            if file_form.is_valid():
                project_file = file_form.save(commit=False)
                project_file.project = project
                project_file.uploaded_by = request.user
                project_file.save()
                record_project_event(
                    project,
                    f"Добавлен файл: {project_file.title}",
                    kind=ProjectEvent.Kind.FILE,
                    actor=request.user,
                    client_visible=project_file.client_visible,
                    project_file=project_file,
                )
                messages.success(request, "Файл добавлен.")
                return redirect("office:project-detail", token=project.public_id)
        elif action == "delete_file":
            project_file = get_object_or_404(
                ProjectFile,
                public_id=request.POST.get("file_id"),
                project=project,
            )
            file_title = project_file.title
            delete_project_file(project_file)
            messages.success(request, f"Файл «{file_title}» удален из проекта и истории.")
            return redirect("office:project-detail", token=project.public_id)
        elif action == "complete_project":
            notify_client = request.POST.get("notify_client") == "1"
            try:
                project, changed = complete_project(
                    project,
                    actor=request.user,
                    notify_client=notify_client,
                )
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("office:project-detail", token=project.public_id)
            if not changed:
                messages.info(request, "Проект уже завершен.")
            elif notify_client:
                messages.success(
                    request,
                    "Проект завершен. Уведомление добавлено в личный кабинет и будет отправлено в Telegram, "
                    "если клиент подключил его.",
                )
            else:
                messages.success(request, "Проект завершен без уведомления клиента.")
            return redirect("office:project-detail", token=project.public_id)

    project.events.filter(manager_seen_at__isnull=True).update(manager_seen_at=timezone.now())
    return render(
        request,
        "office/project_detail.html",
        {
            "project": project,
            "project_form": project_form,
            "stage_form": stage_form,
            "edit_stage": edit_stage,
            "file_form": file_form,
        },
    )


@staff_member_required
def approvals(request):
    stages = ProjectStage.objects.filter(approval_required=True).select_related("project", "approved_by")
    if request.method == "POST":
        stage = get_object_or_404(
            stages,
            pk=request.POST.get("stage_id"),
            approval_state=ProjectStage.ApprovalState.CHANGES_REQUESTED,
        )
        request_stage_approval(stage, actor=request.user, force=True)
        messages.success(request, "Этап повторно отправлен клиенту.")
        return redirect("office:approvals")

    return render(
        request,
        "office/approvals.html",
        {
            "pending_approvals": stages.filter(approval_state=ProjectStage.ApprovalState.PENDING).order_by(
                "due_at", "approval_requested_at"
            ),
            "changes_requested": stages.filter(approval_state=ProjectStage.ApprovalState.CHANGES_REQUESTED).order_by(
                "-approval_decided_at"
            ),
            "approval_history": stages.filter(approval_state=ProjectStage.ApprovalState.APPROVED).order_by(
                "-approval_decided_at"
            )[:40],
        },
    )


@staff_member_required
def calendar_view(request):
    month_start = _selected_month(request.GET.get("month"))
    weeks, next_month = _deadline_calendar(month_start)
    previous_month = (month_start - timedelta(days=1)).replace(day=1)
    today = timezone.localdate()
    overdue = list(reversed(_deadline_events(end_date=today)))[:12]
    upcoming = _deadline_events(start_date=today, end_date=today + timedelta(days=15))[:16]
    return render(
        request,
        "office/calendar.html",
        {
            "weeks": weeks,
            "month_label": f"{MONTH_NAMES[month_start.month]} {month_start.year}",
            "previous_month": previous_month.strftime("%Y-%m"),
            "next_month": next_month.strftime("%Y-%m"),
            "overdue_deadlines": overdue,
            "upcoming_deadlines": upcoming,
        },
    )


@staff_member_required
def clients(request):
    User = get_user_model()
    query = request.GET.get("q", "").strip()
    users = User.objects.filter(is_staff=False, is_superuser=False).order_by("last_name", "first_name", "username")
    if query:
        users = users.filter(
            Q(username__icontains=query)
            | Q(email__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
        )
    items = []
    for user in users[:100]:
        email = (user.email or "").strip()
        project_query = Q(user=user)
        invoice_query = Q(user=user)
        if email:
            project_query |= Q(client_email__iexact=email)
            invoice_query |= Q(client_email__iexact=email)
        items.append(
            {
                "user": user,
                "projects": Project.objects.filter(project_query).distinct().count(),
                "invoices": Invoice.objects.filter(invoice_query).distinct().count(),
            }
        )
    return render(request, "office/clients.html", {"clients": items, "query": query})


@staff_member_required
def client_detail(request, pk):
    User = get_user_model()
    client = get_object_or_404(User, pk=pk, is_staff=False, is_superuser=False)
    email = (client.email or "").strip()
    project_query = Q(user=client)
    invoice_query = Q(user=client)
    conversation_query = Q(user=client)
    site_query = Q(user=client)
    if email:
        project_query |= Q(client_email__iexact=email)
        invoice_query |= Q(client_email__iexact=email)
        conversation_query |= Q(client_email__iexact=email)
        site_query |= Q(order__client_email__iexact=email)
    conversations = Conversation.objects.filter(conversation_query).distinct()
    primary_conversation = conversations.exclude(status=Conversation.Status.CLOSED).first() or conversations.first()
    if request.method == "POST" and request.POST.get("action") == "open_conversation":
        if primary_conversation is None:
            client_name = client.get_full_name().strip() or client.username or email
            primary_conversation = Conversation.objects.create(
                user=client,
                client_email=email,
                title=f"Диалог с {client_name}",
                status=Conversation.Status.WAITING_MANAGER,
            )
            messages.success(request, "Диалог с клиентом создан.")
        return redirect("office:conversation-detail", pk=primary_conversation.pk)

    return render(
        request,
        "office/client_detail.html",
        {
            "client_account": client,
            "projects": Project.objects.filter(project_query).distinct()[:30],
            "invoices": Invoice.objects.filter(invoice_query).distinct()[:30],
            "conversations": conversations[:30],
            "primary_conversation": primary_conversation,
            "sites": ManagedSite.objects.filter(site_query).distinct()[:30],
        },
    )


@staff_member_required
def global_search(request):
    query = request.GET.get("q", "").strip()
    results = {"projects": [], "clients": [], "invoices": [], "conversations": [], "sites": []}
    if len(query) >= 2:
        User = get_user_model()
        results = {
            "projects": Project.objects.filter(
                Q(title__icontains=query) | Q(client_name__icontains=query) | Q(client_email__icontains=query)
            )[:20],
            "clients": User.objects.filter(
                Q(username__icontains=query)
                | Q(email__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query),
                is_staff=False,
            )[:20],
            "invoices": Invoice.objects.filter(
                Q(title__icontains=query) | Q(client_name__icontains=query) | Q(client_email__icontains=query)
            )[:20],
            "conversations": Conversation.objects.filter(Q(title__icontains=query) | Q(client_email__icontains=query))[
                :20
            ],
            "sites": ManagedSite.objects.filter(Q(title__icontains=query) | Q(domain_name__icontains=query))[:20],
        }
    return render(request, "office/search.html", {"query": query, "results": results})


@staff_member_required
def revenue(request):
    succeeded = Payment.objects.filter(status=Payment.Status.SUCCEEDED, paid_at__isnull=False)
    current_month = timezone.localdate().replace(day=1)
    monthly = list(
        succeeded.annotate(month=TruncMonth("paid_at"))
        .values("month")
        .annotate(total=Sum("amount"), payments=Count("id"))
        .order_by("-month")[:12]
    )
    outstanding = Invoice.objects.filter(status=Invoice.Status.ISSUED).aggregate(total=Sum("amount"))["total"] or 0
    top_services = list(
        succeeded.values("invoice__title").annotate(total=Sum("amount"), payments=Count("id")).order_by("-total")[:8]
    )
    return render(
        request,
        "office/revenue.html",
        {
            "total": succeeded.aggregate(total=Sum("amount"))["total"] or 0,
            "month_total": succeeded.filter(paid_at__date__gte=current_month).aggregate(total=Sum("amount"))["total"]
            or 0,
            "outstanding": outstanding,
            "needs_review": Payment.objects.filter(status=Payment.Status.REVIEW).count(),
            "monthly": monthly,
            "top_services": top_services,
        },
    )


@never_cache
@base_staff_member_required
def security(request):
    profile = get_profile(request.user)
    generated_codes = request.session.pop("new_backup_codes", None)
    setup_required = needs_totp_setup(request.user)
    pending_secret = request.session.get("pending_totp_secret", "")
    rotation_secret = request.session.get("pending_totp_rotation_secret", "")
    if setup_required and not pending_secret:
        pending_secret = generate_totp_secret()
        request.session["pending_totp_secret"] = pending_secret

    password_form = SecurityPasswordForm(user=request.user)
    totp_confirm_form = TotpSetupConfirmForm(user=request.user, secret=pending_secret)
    rotation_start_form = TotpSetupConfirmForm(user=request.user, secret=profile.get_totp_secret())
    rotation_confirm_form = TotpSetupConfirmForm(user=request.user, secret=rotation_secret)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "confirm_totp_setup" and setup_required:
            totp_confirm_form = TotpSetupConfirmForm(request.POST, user=request.user, secret=pending_secret)
            if totp_confirm_form.is_valid():
                profile.set_totp_secret(pending_secret)
                profile.totp_enabled = True
                profile.save(update_fields=("totp_secret", "totp_enabled"))
                request.session.pop("pending_totp_secret", None)
                request.session["new_backup_codes"] = replace_backup_codes(request.user)
                messages.success(request, "TOTP включен. Сохраните резервные коды в надежном месте.")
                return redirect("office:security")
        elif action == "start_totp_rotation" and not setup_required and not rotation_secret:
            rotation_start_form = TotpSetupConfirmForm(
                request.POST,
                user=request.user,
                secret=profile.get_totp_secret(),
            )
            if rotation_start_form.is_valid():
                request.session["pending_totp_rotation_secret"] = generate_totp_secret()
                messages.info(request, "Добавьте новый ключ в приложение и подтвердите первый код.")
                return redirect("office:security")
        elif action == "confirm_totp_rotation" and rotation_secret:
            rotation_confirm_form = TotpSetupConfirmForm(
                request.POST,
                user=request.user,
                secret=rotation_secret,
            )
            if rotation_confirm_form.is_valid():
                profile.set_totp_secret(rotation_secret)
                profile.totp_enabled = True
                profile.save(update_fields=("totp_secret", "totp_enabled"))
                request.session.pop("pending_totp_rotation_secret", None)
                request.session["new_backup_codes"] = replace_backup_codes(request.user)
                messages.success(request, "TOTP-ключ и резервные коды обновлены.")
                return redirect("office:security")
        elif action == "cancel_totp_rotation":
            request.session.pop("pending_totp_rotation_secret", None)
            messages.info(request, "Обновление ключа отменено. Старый ключ продолжает работать.")
            return redirect("office:security")
        elif action == "regenerate_backup_codes":
            password_form = SecurityPasswordForm(request.POST, user=request.user)
            if password_form.is_valid():
                request.session["new_backup_codes"] = replace_backup_codes(request.user)
                messages.success(request, "Резервные коды выпущены заново. Старые коды больше не работают.")
                return redirect("office:security")
        else:
            messages.error(request, "Неизвестное действие безопасности.")

    display_secret = pending_secret if setup_required else rotation_secret
    return render(
        request,
        "office/security.html",
        {
            "profile": profile,
            "password_form": password_form,
            "totp_confirm_form": totp_confirm_form,
            "rotation_start_form": rotation_start_form,
            "rotation_confirm_form": rotation_confirm_form,
            "backup_stats": backup_code_stats(request.user),
            "generated_codes": generated_codes,
            "totp_setup_required": setup_required,
            "totp_rotation_in_progress": bool(rotation_secret),
            "totp_secret": display_secret,
            "otpauth_uri": build_otpauth_uri(request.user, display_secret) if display_secret else "",
        },
    )


def _telegram_action_result(title, result):
    return {
        "title": title,
        "ok": bool(result.get("ok")),
        "body": json.dumps(result, ensure_ascii=False, indent=2),
    }


def _telegram_chat_candidates(updates_result):
    candidates = {}
    for update in updates_result.get("result", []):
        source = None
        for key in ("message", "edited_message", "channel_post", "edited_channel_post", "my_chat_member"):
            if update.get(key, {}).get("chat"):
                source = update[key]
                break
        if not source and update.get("callback_query", {}).get("message", {}).get("chat"):
            source = update["callback_query"]["message"]
        if not source:
            continue
        chat = source.get("chat", {})
        chat_id = chat.get("id")
        if chat_id is None:
            continue
        label_parts = [
            chat.get("title"),
            " ".join(part for part in (chat.get("first_name"), chat.get("last_name")) if part).strip(),
            f"@{chat.get('username')}" if chat.get("username") else "",
        ]
        label = next((part for part in label_parts if part), "чат без имени")
        candidates[str(chat_id)] = {
            "id": str(chat_id),
            "type": chat.get("type", "unknown"),
            "label": label,
            "update_id": update.get("update_id"),
            "message": (source.get("text") or source.get("caption") or "")[:120],
        }
    return list(candidates.values())


def _public_base_url(request):
    configured = (getattr(settings, "AURUMWEB_SITE_URL", "") or "").strip().rstrip("/")
    return configured or request.build_absolute_uri("/").rstrip("/")


def _robokassa_context(request):
    client = RobokassaClient()
    config = client.config
    base_url = _public_base_url(request)
    callbacks = [
        {
            "label": "Подтверждение оплаты",
            "url": f"{base_url}/billing/robokassa/result/",
            "method": "POST",
            "purpose": "серверное подтверждение оплаты, именно оно меняет статус счета",
        },
        {
            "label": "Успешная оплата",
            "url": f"{base_url}/billing/robokassa/success/",
            "method": "POST или GET",
            "purpose": "возврат клиента после успешной оплаты",
        },
        {
            "label": "Неуспешная оплата",
            "url": f"{base_url}/billing/robokassa/fail/",
            "method": "POST или GET",
            "purpose": "возврат клиента после отмены или ошибки оплаты",
        },
    ]
    return {
        "configured": client.configured,
        "test_mode": config.test_mode,
        "hash_algorithm": config.hash_algorithm.upper(),
        "payment_url": config.payment_url,
        "receipt_enabled": config.receipt_enabled,
        "receipt_tax": config.receipt_tax,
        "receipt_payment_method": config.receipt_payment_method,
        "receipt_payment_object": config.receipt_payment_object,
        "merchant_login_set": bool(config.merchant_login),
        "password1_set": bool(config.password1),
        "password2_set": bool(config.password2),
        "pending_reconciliation": Payment.objects.filter(
            provider=Payment.Provider.ROBOKASSA,
            status=Payment.Status.PENDING,
        ).count(),
        "requires_review": Payment.objects.filter(
            provider=Payment.Provider.ROBOKASSA,
            status=Payment.Status.REVIEW,
        ).count(),
        "callbacks": callbacks,
    }


@staff_member_required
def integrations(request):
    config = TelegramBotSettings.load()
    quick_config = QuickConsultationBotSettings.load()
    robokassa_config = RobokassaSettings.load()
    form = TelegramIntegrationForm(user=request.user, config=config)
    quick_form = QuickConsultationTelegramForm(user=request.user, config=quick_config)
    robokassa_form = RobokassaIntegrationForm(user=request.user, config=robokassa_config)
    telegram_result = None
    telegram_chats = []
    quick_telegram_result = None
    quick_telegram_chats = []

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "save_telegram":
            form = TelegramIntegrationForm(request.POST, user=request.user, config=config)
            if form.is_valid():
                config = form.save()
                messages.success(request, "Настройки Telegram-бота сохранены. Секреты скрыты звездочками.")
                return redirect("office:integrations")
        elif action == "save_quick_telegram":
            quick_form = QuickConsultationTelegramForm(request.POST, user=request.user, config=quick_config)
            if quick_form.is_valid():
                quick_config = quick_form.save()
                messages.success(request, "Настройки бота быстрых консультаций сохранены. Секреты скрыты звездочками.")
                return redirect("office:integrations")
        elif action == "save_robokassa":
            robokassa_form = RobokassaIntegrationForm(request.POST, user=request.user, config=robokassa_config)
            if robokassa_form.is_valid():
                robokassa_config = robokassa_form.save()
                IntegrationEvent.objects.create(
                    provider=IntegrationEvent.Provider.ROBOKASSA,
                    status=IntegrationEvent.Status.SUCCESS,
                    title="Настройки Robokassa обновлены",
                    payload={
                        "configured": robokassa_config.configured,
                        "test_mode": robokassa_config.test_mode,
                        "receipt_enabled": robokassa_config.receipt_enabled,
                    },
                )
                messages.success(request, "Настройки Robokassa сохранены. Пароли скрыты звездочками.")
                return redirect("office:integrations")
        elif action == "telegram_set_chat_id":
            chat_id = request.POST.get("chat_id", "").strip()
            if chat_id:
                config.admin_chat_id = chat_id
                config.save(update_fields=("admin_chat_id", "updated_at"))
                messages.success(
                    request, f"Номер чата {chat_id} сохранен. Теперь можно включить уведомления и отправить тест."
                )
                return redirect("office:integrations")
            messages.error(request, "Номер чата не найден в запросе.")
        elif action == "quick_telegram_set_chat_id":
            chat_id = request.POST.get("chat_id", "").strip()
            if chat_id:
                quick_config.admin_chat_id = chat_id
                quick_config.save(update_fields=("admin_chat_id", "updated_at"))
                messages.success(
                    request,
                    f"Номер чата {chat_id} сохранен для бота быстрых консультаций. Теперь можно включить уведомления.",
                )
                return redirect("office:integrations")
            messages.error(request, "Номер чата не найден в запросе.")
        else:
            client = QuickConsultationBotClient() if action.startswith("quick_telegram_") else TelegramBotClient()
            if action == "telegram_check":
                telegram_result = _telegram_action_result("Проверка бота", client.get_me())
            elif action == "quick_telegram_check":
                quick_telegram_result = _telegram_action_result("Проверка бота быстрых консультаций", client.get_me())
            elif action == "telegram_updates":
                updates_result = client.get_updates(limit=20)
                telegram_chats = _telegram_chat_candidates(updates_result)
                telegram_result = _telegram_action_result("Последние сообщения боту", updates_result)
            elif action == "quick_telegram_updates":
                updates_result = client.get_updates(limit=20)
                quick_telegram_chats = _telegram_chat_candidates(updates_result)
                quick_telegram_result = _telegram_action_result("Последние сообщения боту консультаций", updates_result)
            elif action == "telegram_webhook_info":
                telegram_result = _telegram_action_result("Состояние приема сообщений", client.get_webhook_info())
            elif action == "quick_telegram_webhook_info":
                quick_telegram_result = _telegram_action_result(
                    "Состояние приема сообщений бота консультаций", client.get_webhook_info()
                )
            elif action == "telegram_set_webhook":
                telegram_result = _telegram_action_result("Прием сообщений включен", client.set_webhook())
            elif action == "quick_telegram_set_webhook":
                quick_telegram_result = _telegram_action_result(
                    "Прием сообщений бота консультаций включен", client.set_webhook(drop_pending_updates=True)
                )
            elif action == "telegram_delete_webhook":
                telegram_result = _telegram_action_result("Прием сообщений отключен", client.delete_webhook())
            elif action == "quick_telegram_delete_webhook":
                quick_telegram_result = _telegram_action_result(
                    "Прием сообщений бота консультаций отключен", client.delete_webhook()
                )
            elif action == "telegram_send_test":
                ok = client.send_message("AurumWeb: тестовое уведомление из админки.")
                telegram_result = _telegram_action_result("Тестовое сообщение", {"ok": ok})
            elif action == "quick_telegram_send_test":
                ok = client.send_message("AurumWeb Fast: тестовое уведомление быстрых консультаций.")
                quick_telegram_result = _telegram_action_result("Тестовое сообщение бота консультаций", {"ok": ok})
            elif action == "telegram_brand_setup":
                telegram_result = _telegram_action_result("Бренд бота", client.apply_aurumweb_branding())
            elif action == "quick_telegram_brand_setup":
                quick_telegram_result = _telegram_action_result(
                    "Бренд бота быстрых консультаций", client.apply_aurumweb_branding()
                )
            elif action == "telegram_process_updates":
                telegram_result = _telegram_action_result(
                    "Команды Telegram", process_pending_telegram_updates(limit=20)
                )
            elif action == "quick_telegram_process_updates":
                quick_telegram_result = _telegram_action_result(
                    "Команды бота быстрых консультаций", process_pending_quick_telegram_updates(limit=20)
                )
            else:
                messages.error(request, "Неизвестное действие интеграции.")

            if telegram_result:
                config.refresh_from_db()
                if telegram_result["ok"]:
                    messages.success(request, f"{telegram_result['title']}: выполнено.")
                else:
                    messages.error(request, f"{telegram_result['title']}: нужна проверка настроек.")
            if quick_telegram_result:
                quick_config.refresh_from_db()
                if quick_telegram_result["ok"]:
                    messages.success(request, f"{quick_telegram_result['title']}: выполнено.")
                else:
                    messages.error(request, f"{quick_telegram_result['title']}: нужна проверка настроек.")

    events = IntegrationEvent.objects.filter(provider=IntegrationEvent.Provider.TELEGRAM).order_by("-created_at")[:8]
    robokassa_events = IntegrationEvent.objects.filter(provider=IntegrationEvent.Provider.ROBOKASSA).order_by(
        "-created_at"
    )[:8]
    return render(
        request,
        "office/integrations.html",
        {
            "config": config,
            "quick_config": quick_config,
            "form": form,
            "quick_form": quick_form,
            "robokassa_form": robokassa_form,
            "robokassa": _robokassa_context(request),
            "robokassa_events": robokassa_events,
            "telegram_result": telegram_result,
            "telegram_chats": telegram_chats,
            "quick_telegram_result": quick_telegram_result,
            "quick_telegram_chats": quick_telegram_chats,
            "events": events,
        },
    )


@staff_member_required
def leads(request):
    status = request.GET.get("status")
    query = request.GET.get("q", "").strip()
    items = Lead.objects.all()
    if status:
        items = items.filter(status=status)
    if query:
        items = items.filter(Q(name__icontains=query) | Q(email__icontains=query) | Q(task__icontains=query))
    return render(request, "office/leads.html", {"leads": items[:80], "statuses": Lead.Status.choices})


@staff_member_required
def quick_consultations(request):
    status = request.GET.get("status")
    query = request.GET.get("q", "").strip()
    items = QuickConsultation.objects.select_related("invoice")
    if status:
        items = items.filter(status=status)
    if query:
        items = items.filter(
            Q(client_name__icontains=query)
            | Q(telegram_username__icontains=query)
            | Q(telegram_chat_id__icontains=query)
            | Q(question__icontains=query)
        )
    return render(
        request,
        "office/quick_consultations.html",
        {
            "consultations": items[:100],
            "statuses": QuickConsultation.Status.choices,
            "quick_start_url": QuickConsultationBotClient().quick_consultation_start_url(),
        },
    )


@staff_member_required
def quick_consultation_detail(request, pk):
    consultation = get_object_or_404(QuickConsultation.objects.select_related("invoice"), pk=pk)
    if request.method == "POST":
        action = request.POST.get("action", "save")
        form = QuickConsultationForm(request.POST, instance=consultation)
        if form.is_valid():
            if action == "send_message":
                consultation = form.save()
                if send_quick_consultation_message(consultation, form.cleaned_data["manager_response"]):
                    messages.success(request, "Сообщение отправлено клиенту в Telegram.")
                else:
                    messages.error(request, "Не удалось отправить сообщение: проверьте Telegram-настройки.")
                return redirect("office:quick-consultation-detail", pk=consultation.pk)
            if action == "create_invoice":
                if not form.cleaned_data.get("quoted_amount"):
                    form.add_error("quoted_amount", "Укажите стоимость консультации.")
                else:
                    consultation = form.save()
                    invoice, _payment_url = invoice_quick_consultation(consultation, form.cleaned_data)
                    messages.success(request, f"Счет #{invoice.pk} создан и отправлен клиенту в Telegram.")
                    return redirect("office:quick-consultation-detail", pk=consultation.pk)
            elif action == "save":
                consultation = form.save()
                messages.success(request, "Консультация сохранена.")
                return redirect("office:quick-consultation-detail", pk=consultation.pk)
        messages.error(request, "Проверьте поля формы.")
    else:
        form = QuickConsultationForm(instance=consultation)
    return render(
        request,
        "office/quick_consultation_detail.html",
        {"consultation": consultation, "form": form},
    )


@staff_member_required
def lead_detail(request, pk):
    lead = get_object_or_404(Lead, pk=pk)
    order = lead.orders.first()
    conversations_list = lead.conversations.prefetch_related("messages")
    metadata = lead_metadata(lead)
    selected_template = metadata.get("selected_template")
    brief = metadata.get("public_brief", {})
    demo_request = metadata.get("demo_request", {})
    proposal_draft = metadata.get("proposal_draft")
    qualification_defaults = qualification_initial(lead)
    qualification = metadata.get("qualification") or qualification_defaults

    form = LeadUpdateForm(instance=lead)
    qualification_form = LeadQualificationForm(initial=qualification_defaults)

    if request.method == "POST":
        action = request.POST.get("action", "lead")
        if action == "close_lead":
            was_closed = lead.status == Lead.Status.LOST
            close_lead(lead)
            if was_closed:
                messages.info(request, "Заявка уже была закрыта.")
            else:
                messages.success(request, "Заявка закрыта.")
            return _redirect_after_action(request, "office:lead-detail", pk=lead.pk)
        if action == "qualification":
            qualification_form = LeadQualificationForm(request.POST)
            if qualification_form.is_valid():
                save_lead_qualification(lead, qualification_form.cleaned_data)
                messages.success(request, "CRM-разбор заявки сохранен.")
                return redirect("office:lead-detail", pk=lead.pk)
        elif action == "build_proposal":
            qualification = metadata.get("qualification") or qualification_defaults
            store_proposal_draft(lead, qualification, selected_template, request.user)
            messages.success(request, "Черновик КП собран из заявки и чеклиста.")
            return redirect("office:lead-detail", pk=lead.pk)
        else:
            form = LeadUpdateForm(request.POST, instance=lead)
            if form.is_valid():
                form.save()
                messages.success(request, "Заявка обновлена.")
                return redirect("office:lead-detail", pk=lead.pk)

    return render(
        request,
        "office/lead_detail.html",
        {
            "lead": lead,
            "form": form,
            "qualification_form": qualification_form,
            "order": order,
            "conversations": conversations_list,
            "selected_template": selected_template,
            "brief": brief,
            "demo_request": demo_request,
            "qualification": qualification,
            "proposal_draft": proposal_draft,
            "checklist_fields": CHECKLIST_FIELDS,
        },
    )


@staff_member_required
def create_invoice(request, pk):
    lead = get_object_or_404(Lead, pk=pk)
    proposal_draft = lead_metadata(lead).get("proposal_draft", {})
    client_requisites = client_requisites_from_lead(lead)
    initial = {
        "title": lead.subject or lead.service_type or f"Счет по заявке #{lead.pk}",
        "description": proposal_draft.get("body") or lead.task,
        "amount": "0.00",
    }
    initial["item_name"] = initial["title"]
    if request.method == "POST":
        form = InvoiceCreateForm(request.POST)
        if form.is_valid():
            invoice = create_invoice_draft_for_lead(lead, form.cleaned_data)
            messages.success(request, "Черновик счета и заказ созданы.")
            return redirect("office:invoice-detail", pk=invoice.pk)
    else:
        form = InvoiceCreateForm(initial=initial)
    return render(
        request,
        "office/create_invoice.html",
        {"lead": lead, "form": form, "client_requisites": client_requisites},
    )


@staff_member_required
def conversations(request):
    status = request.GET.get("status")
    items = Conversation.objects.prefetch_related("messages").select_related("lead", "user")
    if status:
        items = items.filter(status=status)
    return render(
        request, "office/conversations.html", {"conversations": items[:80], "statuses": Conversation.Status.choices}
    )


@staff_member_required
def conversation_detail(request, pk):
    conversation = get_object_or_404(Conversation.objects.prefetch_related("messages"), pk=pk)
    Conversation.objects.filter(pk=conversation.pk).update(manager_last_read_at=timezone.now())
    selected_template = None
    if conversation.lead_id:
        selected_template = conversation.lead.metadata.get("selected_template")
    if request.method == "POST":
        action = request.POST.get("action", "reply")
        if action == "close_conversation":
            was_closed = conversation.status == Conversation.Status.CLOSED
            close_conversation(conversation)
            if was_closed:
                messages.info(request, "Диалог уже был закрыт.")
            else:
                messages.success(request, "Диалог закрыт.")
            return _redirect_after_action(request, "office:conversation-detail", pk=conversation.pk)
        form = ManagerMessageForm(request.POST)
        if form.is_valid():
            add_manager_reply(conversation, request.user, form.cleaned_data["body"])
            messages.success(request, "Ответ добавлен в диалог.")
            return redirect("office:conversation-detail", pk=conversation.pk)
    else:
        form = ManagerMessageForm()
    return render(
        request,
        "office/conversation_detail.html",
        {"conversation": conversation, "form": form, "selected_template": selected_template},
    )


@staff_member_required
def conversation_client_preview(request, pk):
    conversation = get_object_or_404(Conversation.objects.prefetch_related("messages"), pk=pk)
    email = conversation_email(conversation)
    client_user = conversation_user(conversation, email)
    profile = find_profile(client_user)
    lead = conversation.lead
    metadata = lead_metadata(lead) if lead else {}
    public_brief = metadata.get("public_brief", {})
    demo_request = metadata.get("demo_request", {})
    selected_template = None
    if lead:
        selected_template = metadata.get("selected_template")

    if request.method == "POST":
        action = request.POST.get("action", "")
        if not profile:
            messages.error(request, "У клиента пока нет профиля, Telegram-привязку проверить нельзя.")
            return redirect("office:conversation-client-preview", pk=conversation.pk)
        if action == "telegram_client_test":
            ok = send_client_telegram_test(profile)
            if ok:
                messages.success(request, "Тестовое Telegram-уведомление отправлено клиенту.")
            else:
                messages.error(request, "Тест не отправлен: Telegram клиента не привязан или уведомления выключены.")
            return redirect("office:conversation-client-preview", pk=conversation.pk)
        if action == "telegram_client_refresh_code":
            refresh_client_telegram_code(profile)
            messages.success(request, "Код подключения Telegram для клиента обновлен.")
            return redirect("office:conversation-client-preview", pk=conversation.pk)
        if action == "telegram_client_disconnect":
            disconnect_client_telegram(profile)
            messages.success(request, "Telegram клиента отключен. Новый код подключения создан.")
            return redirect("office:conversation-client-preview", pk=conversation.pk)

    telegram_start_url = ""
    if profile and not profile.telegram_linked:
        if not profile.telegram_link_code:
            profile.ensure_telegram_link_code()
        telegram_start_url = TelegramBotClient().client_start_url(profile.telegram_link_code)

    lead_query = Q(pk__isnull=True)
    if client_user:
        lead_query |= Q(user=client_user)
    if email:
        lead_query |= Q(email__iexact=email)
    if lead:
        lead_query |= Q(pk=lead.pk)

    conversation_query = Q(pk=conversation.pk)
    if client_user:
        conversation_query |= Q(user=client_user)
    if email:
        conversation_query |= Q(client_email__iexact=email)
    if lead:
        conversation_query |= Q(lead=lead)

    order_query = Q(pk__isnull=True)
    if client_user:
        order_query |= Q(user=client_user)
    if email:
        order_query |= Q(client_email__iexact=email)
    if lead:
        order_query |= Q(lead=lead)

    invoice_query = Q(pk__isnull=True)
    if client_user:
        invoice_query |= Q(user=client_user)
    if email:
        invoice_query |= Q(client_email__iexact=email)
    if lead:
        invoice_query |= Q(lead=lead)

    site_query = Q(pk__isnull=True)
    if client_user:
        site_query |= Q(user=client_user)
    if email:
        site_query |= Q(order__client_email__iexact=email)
    if lead:
        site_query |= Q(lead=lead)

    contact_from_brief = public_brief.get("contact") or demo_request.get("reply_contact") or ""
    contact_lower = contact_from_brief.lower()
    phone_from_brief = contact_from_brief if any(char.isdigit() for char in contact_from_brief) else ""
    telegram_from_brief = (
        contact_from_brief
        if "@" in contact_lower or "t.me" in contact_lower or "telegram" in contact_lower or "tg:" in contact_lower
        else ""
    )
    client_name = (
        (client_user.get_full_name() if client_user else "")
        or (profile.payer_name if profile else "")
        or (lead.name if lead else "")
        or demo_request.get("client_name")
        or "Клиент без имени"
    )
    client_summary = {
        "name": client_name,
        "email": email or "не указан",
        "phone": (profile.phone if profile and profile.phone else phone_from_brief) or "не указан",
        "telegram": (profile.telegram_username if profile and profile.telegram_username else telegram_from_brief)
        or "не указан",
        "telegram_linked": "привязан" if profile and profile.telegram_chat_id else "не привязан",
        "telegram_notifications": ("включены" if profile and profile.telegram_notifications_enabled else "выключены"),
        "brief_contact": contact_from_brief or "не указан",
        "account": "есть" if client_user else "нет аккаунта",
        "requisites": "заполнены" if profile and profile.requisites_complete else "не заполнены",
        "client_type": profile.get_client_type_display() if profile else "не указан",
        "payer_name": profile.payer_name if profile else "не указан",
    }

    leads = Lead.objects.filter(lead_query).distinct().order_by("-created_at")[:8]
    conversations = Conversation.objects.filter(conversation_query).distinct().order_by("-updated_at")[:8]
    orders = Order.objects.filter(order_query).select_related("invoice", "lead").distinct().order_by("-created_at")[:8]
    invoices = Invoice.objects.filter(invoice_query).select_related("lead").distinct().order_by("-created_at")[:8]
    sites = (
        ManagedSite.objects.filter(site_query)
        .select_related("order", "lead")
        .distinct()
        .order_by("status", "title")[:8]
    )

    return render(
        request,
        "office/conversation_client_preview.html",
        {
            "conversation": conversation,
            "selected_template": selected_template,
            "client_summary": client_summary,
            "lead": lead,
            "leads": leads,
            "conversations": conversations,
            "orders": orders,
            "invoices": invoices,
            "sites": sites,
            "client_profile": profile,
            "telegram_start_url": telegram_start_url,
        },
    )


@staff_member_required
def orders(request):
    status = request.GET.get("status")
    query = request.GET.get("q", "").strip()
    items = Order.objects.select_related("lead", "invoice")
    if status:
        items = items.filter(status=status)
    if query:
        items = items.filter(
            Q(title__icontains=query)
            | Q(client_name__icontains=query)
            | Q(client_email__icontains=query)
            | Q(scope__icontains=query)
        )
    return render(request, "office/orders.html", {"orders": items[:80], "statuses": Order.Status.choices})


@staff_member_required
def order_detail(request, pk):
    order = get_object_or_404(
        Order.objects.select_related("lead", "invoice", "user").prefetch_related("managed_sites"), pk=pk
    )
    if request.method == "POST":
        action = request.POST.get("action", "save")
        if action == "complete_order":
            was_completed = order.status == Order.Status.COMPLETED
            complete_order(order)
            if was_completed:
                messages.info(request, "Заказ уже был завершен.")
            else:
                messages.success(request, "Заказ завершен.")
            return _redirect_after_action(request, "office:order-detail", pk=order.pk)
        previous_status = order.status
        form = OrderUpdateForm(request.POST, instance=order)
        if form.is_valid():
            order = save_order_with_notifications(form, previous_status)
            messages.success(request, "Заказ обновлен.")
            return _redirect_after_action(request, "office:order-detail", pk=order.pk)
    else:
        form = OrderUpdateForm(instance=order)
    return render(
        request, "office/order_detail.html", {"order": order, "form": form, "sites": order.managed_sites.all()}
    )


@staff_member_required
@require_POST
def create_site_for_order(request, pk):
    order = get_object_or_404(Order.objects.select_related("lead", "user"), pk=pk)
    site, created = create_managed_site_from_order(order)
    if created:
        messages.success(request, "Карточка сайта создана. Заполните домен, SSL, хостинг и поддержку.")
    else:
        messages.info(request, "Карточка сайта уже была создана для этого заказа.")
    return redirect("office:site-detail", pk=site.pk)


@staff_member_required
def invoices(request):
    status = request.GET.get("status")
    query = request.GET.get("q", "").strip()
    items = Invoice.objects.select_related("lead", "user").prefetch_related("payments")
    if status:
        items = items.filter(status=status)
    if query:
        items = items.filter(
            Q(title__icontains=query)
            | Q(client_name__icontains=query)
            | Q(client_email__icontains=query)
            | Q(description__icontains=query)
        )
    return render(request, "office/invoices.html", {"invoices": items[:80], "statuses": Invoice.Status.choices})


@staff_member_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.prefetch_related("items", "payments"), pk=pk)
    return render(request, "office/invoice_detail.html", {"invoice": invoice})


@staff_member_required
def service_prices(request):
    service_type = request.GET.get("type", "").strip()
    query = request.GET.get("q", "").strip()
    items = Service.objects.all()
    if service_type:
        items = items.filter(service_type=service_type)
    if query:
        items = items.filter(
            Q(title__icontains=query) | Q(short_description__icontains=query) | Q(price_note__icontains=query)
        )
    summary = {
        "published": Service.objects.filter(is_published=True).count(),
        "featured": Service.objects.filter(is_published=True, is_featured=True).count(),
        "priced": Service.objects.filter(is_published=True, price_amount__isnull=False).count(),
    }
    return render(
        request,
        "office/service_prices.html",
        {
            "services": items[:120],
            "service_types": Service.ServiceType.choices,
            "current_type": service_type,
            "query": query,
            "summary": summary,
        },
    )


@staff_member_required
def service_price_detail(request, pk):
    service = get_object_or_404(Service, pk=pk)
    if request.method == "POST":
        form = ServicePriceForm(request.POST, instance=service)
        if form.is_valid():
            service = form.save()
            messages.success(request, f"Цена услуги “{service.title}” обновлена: {service.price_display}.")
            return redirect("office:service-price-detail", pk=service.pk)
    else:
        form = ServicePriceForm(instance=service)
    return render(request, "office/service_price_detail.html", {"service": service, "form": form})


@staff_member_required
def managed_sites(request):
    status = request.GET.get("status")
    query = request.GET.get("q", "").strip()
    items = ManagedSite.objects.select_related("user", "order", "lead")
    if status:
        items = items.filter(status=status)
    if query:
        items = items.filter(
            Q(title__icontains=query)
            | Q(domain_name__icontains=query)
            | Q(url__icontains=query)
            | Q(user__username__icontains=query)
            | Q(user__email__icontains=query)
        )
    return render(request, "office/sites.html", {"sites": items[:80], "statuses": ManagedSite.Status.choices})


@staff_member_required
def managed_site_detail(request, pk):
    site = get_object_or_404(ManagedSite.objects.select_related("user", "order", "lead"), pk=pk)
    if request.method == "POST":
        snapshot = managed_site_update_snapshot(site)
        form = ManagedSiteUpdateForm(request.POST, instance=site)
        if form.is_valid():
            site, _changed_fields = save_managed_site_with_notifications(form, snapshot)
            messages.success(request, "Карточка сайта обновлена.")
            return redirect("office:site-detail", pk=site.pk)
    else:
        form = ManagedSiteUpdateForm(instance=site)
    return render(request, "office/site_detail.html", {"site": site, "form": form})
