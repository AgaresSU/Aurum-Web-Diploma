from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.accounts.forms import ClientRequisitesForm
from apps.accounts.models import Profile
from apps.billing.models import Invoice, ManagedSite, Order
from apps.content.models import TemplateProduct
from apps.core.legal_acceptance import record_legal_acceptance
from apps.core.models import ConsentAcceptance
from apps.core.rate_limit import rate_limited
from apps.integrations.models import IntegrationEvent
from apps.integrations.telegram import TelegramBotClient
from apps.integrations.telegram_handlers import TELEGRAM_PENDING_LINK_TITLE
from apps.leads.models import Lead
from apps.messaging.models import Conversation, Message
from apps.projects.forms import ProjectFileUploadForm
from apps.projects.models import Project, ProjectEvent, ProjectFile, ProjectStage
from apps.projects.services import approve_project_stage, record_project_event, request_project_stage_changes

from .forms import ClientLeadForm, ClientMessageForm

ADMIN_ROLES = {Profile.Role.ADMIN, Profile.Role.MANAGER}


def _status_step(label, is_active, is_done, caption):
    if is_active:
        state = "active"
    elif is_done:
        state = "done"
    else:
        state = "idle"
    return {"label": label, "state": state, "caption": caption}


def _email(user):
    return (user.email or "").strip()


def _client_conversations(user):
    email = _email(user)
    query = Q(user=user)
    if email:
        query |= Q(client_email__iexact=email)
    return Conversation.objects.filter(query).distinct()


def _client_invoices(user):
    email = _email(user)
    query = Q(user=user)
    if email:
        query |= Q(client_email__iexact=email)
    return Invoice.objects.filter(query).distinct()


def _client_orders(user):
    email = _email(user)
    query = Q(user=user)
    if email:
        query |= Q(client_email__iexact=email)
    return Order.objects.filter(query).distinct()


def _client_sites(user):
    email = _email(user)
    query = Q(user=user)
    if email:
        query |= Q(order__client_email__iexact=email)
    return ManagedSite.objects.filter(query).distinct()


def _client_projects(user):
    email = _email(user)
    query = Q(user=user)
    if email:
        query |= Q(client_email__iexact=email)
    return Project.objects.filter(query).distinct()


def _project_dashboard_summary(project):
    if not project:
        return {
            "headline": "Создайте первую задачу",
            "detail": "После заявки здесь появятся этапы, сроки, сообщения и оплаты.",
            "steps": [],
        }
    steps = []
    for stage in project.stages.all():
        if stage.status == ProjectStage.Status.COMPLETED:
            state = "done"
        elif stage.status in (ProjectStage.Status.IN_PROGRESS, ProjectStage.Status.WAITING_CLIENT):
            state = "active"
        else:
            state = "idle"
        steps.append(
            {
                "label": stage.title,
                "state": state,
                "caption": stage.description or stage.get_status_display(),
            }
        )
    return {"headline": project.get_status_display(), "detail": project.title, "steps": steps}


def _project_summary(conversations, orders, invoices, sites):
    latest_order = orders.order_by("-updated_at").first()
    latest_invoice = invoices.order_by("-updated_at").first()
    latest_site = sites.order_by("-updated_at").first()
    latest_conversation = conversations.order_by("-updated_at").first()

    has_conversation = conversations.exists()
    has_order = orders.exists()
    has_invoice = invoices.exists()
    has_paid_invoice = invoices.filter(status=Invoice.Status.PAID).exists()
    has_active_site = sites.filter(status__in=(ManagedSite.Status.LIVE, ManagedSite.Status.MAINTENANCE)).exists()
    has_work = (
        orders.filter(status__in=(Order.Status.IN_PROGRESS, Order.Status.COMPLETED)).exists()
        or sites.filter(
            status__in=(
                ManagedSite.Status.DEVELOPMENT,
                ManagedSite.Status.REVIEW,
                ManagedSite.Status.LIVE,
                ManagedSite.Status.MAINTENANCE,
            )
        ).exists()
    )

    if latest_site:
        headline = latest_site.get_status_display()
        detail = latest_site.title
    elif latest_order:
        headline = latest_order.get_status_display()
        detail = latest_order.title
    elif latest_invoice:
        headline = latest_invoice.get_status_display()
        detail = latest_invoice.title
    elif latest_conversation:
        headline = latest_conversation.get_status_display()
        detail = latest_conversation.title
    else:
        headline = "Нужно создать первую задачу"
        detail = "После заявки здесь появится состояние проекта, счетов и сайта."

    steps = [
        _status_step(
            "Заявка",
            not has_order and has_conversation,
            has_order or has_invoice or has_work,
            "Описание задачи и первый диалог.",
        ),
        _status_step(
            "Разбор",
            latest_order and latest_order.status in {Order.Status.DISCOVERY, Order.Status.PROPOSAL},
            has_invoice or has_work,
            "Уточнение состава работ и стоимости.",
        ),
        _status_step(
            "Счет",
            latest_invoice and latest_invoice.status != Invoice.Status.PAID,
            has_paid_invoice or has_work,
            "Счет появится после согласования.",
        ),
        _status_step(
            "В работе",
            bool(latest_order and latest_order.status == Order.Status.IN_PROGRESS)
            or bool(latest_site and latest_site.status in {ManagedSite.Status.DEVELOPMENT, ManagedSite.Status.REVIEW}),
            has_active_site,
            "Сборка, проверка и подготовка запуска.",
        ),
        _status_step("Поддержка", has_active_site, False, "Домен, SSL, хостинг, бэкапы и правки."),
    ]
    return {"headline": headline, "detail": detail, "steps": steps}


def _client_next_action(projects, invoices, conversations, sites, active_project=None):
    pending_stage = (
        ProjectStage.objects.filter(
            project__in=projects,
            approval_required=True,
            approval_state=ProjectStage.ApprovalState.PENDING,
        )
        .select_related("project")
        .order_by("due_at", "sort_order")
        .first()
    )
    if pending_stage:
        return {
            "title": f"Согласуйте этап «{pending_stage.title}»",
            "text": "Проверьте результат. Его можно согласовать или вернуть с комментарием.",
            "url": reverse("client_portal:approvals"),
            "label": "Открыть согласования",
            "kind": "approval",
        }

    issued_invoice = invoices.filter(status=Invoice.Status.ISSUED).order_by("due_date", "created_at").first()
    if issued_invoice:
        return {
            "title": f"Оплатите счет «{issued_invoice.title}»",
            "text": f"Сумма к оплате: {issued_invoice.amount} ₽.",
            "url": reverse("client_portal:invoice-detail", args=(issued_invoice.public_token,)),
            "label": "Открыть счет",
            "kind": "payment",
        }

    waiting_conversation = conversations.filter(status=Conversation.Status.WAITING_CLIENT).first()
    if waiting_conversation:
        return {
            "title": "Ответьте менеджеру",
            "text": waiting_conversation.title,
            "url": reverse("client_portal:conversation-detail", args=(waiting_conversation.public_token,)),
            "label": "Открыть сообщение",
            "kind": "message",
        }

    if active_project and active_project.next_action:
        return {
            "title": active_project.next_action,
            "text": active_project.title,
            "url": reverse("client_portal:project-detail", args=(active_project.public_id,)),
            "label": "Открыть проект",
            "kind": "project",
        }

    if not projects.exists() and not conversations.exists():
        return {
            "title": "Создайте первую задачу",
            "text": "Опишите сайт, поддержку, Python-программу или Telegram-бота, и менеджер откроет рабочий диалог.",
            "url": reverse("client_portal:create-request"),
            "label": "Создать новую задачу",
            "kind": "request",
        }

    expiring_site = sites.filter(
        Q(domain_expires_at__lte=timezone.localdate() + timedelta(days=30))
        | Q(hosting_expires_at__lte=timezone.localdate() + timedelta(days=30))
    ).first()
    if expiring_site:
        return {
            "title": "Проверьте срок домена или размещения",
            "text": expiring_site.title,
            "url": reverse("client_portal:site-detail", args=(expiring_site.pk,)),
            "label": "Открыть сайт",
            "kind": "site",
        }

    if active_project:
        return {
            "title": "Проверьте ход проекта",
            "text": active_project.title,
            "url": reverse("client_portal:project-detail", args=(active_project.public_id,)),
            "label": "Открыть проект",
            "kind": "project",
        }

    return {
        "title": "Продолжите диалог",
        "text": "Ответьте менеджеру или уточните детали задачи в переписке.",
        "url": reverse("client_portal:conversations"),
        "label": "Открыть диалоги",
        "kind": "message",
    }


def _handle_stage_decision(request, stages):
    stage = get_object_or_404(
        stages,
        pk=request.POST.get("stage_id"),
        approval_required=True,
        approval_state=ProjectStage.ApprovalState.PENDING,
    )
    action = request.POST.get("action", "")
    try:
        if action == "approve_stage":
            approve_project_stage(stage, actor=request.user)
            messages.success(request, "Этап согласован.")
        elif action == "request_stage_changes":
            comment = request.POST.get("comment", "").strip()
            if len(comment) > 2000:
                raise ValueError("Комментарий должен быть короче 2000 символов.")
            request_project_stage_changes(stage, actor=request.user, comment=comment)
            messages.success(request, "Комментарий отправлен менеджеру.")
        else:
            return False
    except ValueError as error:
        messages.error(request, str(error))
    return True


def _bind_conversation(conversation, user):
    if conversation.user_id is None and _email(user) and conversation.client_email.lower() == _email(user).lower():
        conversation.user = user
        conversation.save(update_fields=("user", "updated_at"))
    return conversation


def _profile(user):
    profile, _created = Profile.objects.get_or_create(user=user)
    return profile


def _is_admin_user(user):
    if user.is_staff or user.is_superuser:
        return True
    try:
        return user.profile.role in ADMIN_ROLES
    except Profile.DoesNotExist:
        return False


def client_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if _is_admin_user(request.user):
            return redirect("office:dashboard")
        return view_func(request, *args, **kwargs)

    return login_required(_wrapped)


def _selected_template(slug):
    if not slug:
        return None
    return TemplateProduct.objects.filter(
        slug=slug,
        is_published=True,
        template_type=TemplateProduct.TemplateType.WEBSITE,
    ).first()


def _template_metadata(template, request):
    if not template:
        return {}
    return {
        "selected_template": {
            "slug": template.slug,
            "title": template.title,
            "category": template.category,
            "industry": template.industry,
            "conversion_focus": template.conversion_focus,
            "demo_url": request.build_absolute_uri(reverse("template-site-demo", kwargs={"slug": template.slug})),
        }
    }


def _template_initial(template):
    if not template:
        return {}
    task_lines = [
        f"Хочу обсудить реализацию сайта на базе примера «{template.title}».",
        f"Ниша: {template.industry or template.category}.",
    ]
    if template.conversion_focus:
        task_lines.append(f"Главная цель: {template.conversion_focus}.")
    task_lines.extend(
        [
            "",
            "Нужно понять этапы, бюджет, сроки, состав работ и поддержку после запуска.",
        ]
    )
    return {
        "template_slug": template.slug,
        "service_type": "Сайт на базе шаблона",
        "subject": f"Сайт на базе шаблона: {template.title}",
        "task": "\n".join(task_lines),
    }


@client_required
def telegram_connect(request):
    client_profile = _profile(request.user)
    code = client_profile.ensure_telegram_link_code()
    IntegrationEvent.objects.create(
        provider=IntegrationEvent.Provider.TELEGRAM,
        status=IntegrationEvent.Status.INFO,
        title=TELEGRAM_PENDING_LINK_TITLE,
        payload={"user_id": request.user.pk},
        payload_expires_at=timezone.now() + timedelta(minutes=20),
    )
    telegram = TelegramBotClient()

    if telegram.token_configured:
        telegram.get_me()

    start_url = telegram.client_start_url(code)
    if not start_url:
        messages.error(request, "Telegram-бот пока не настроен. Попробуйте позже или напишите менеджеру в диалоге.")
        return redirect("client_portal:profile")

    return redirect(start_url)


@client_required
def dashboard(request):
    projects = _client_projects(request.user).prefetch_related("stages", "events")
    conversations = _client_conversations(request.user).prefetch_related("messages")
    invoices = _client_invoices(request.user).prefetch_related("payments")
    orders = _client_orders(request.user).select_related("invoice", "lead").prefetch_related("managed_sites")
    sites = _client_sites(request.user).select_related("order")
    open_invoices = invoices.filter(status=Invoice.Status.ISSUED).count()
    conversation_count = conversations.count()
    order_count = orders.count()
    site_count = sites.count()
    active_project = projects.exclude(status__in=(Project.Status.COMPLETED, Project.Status.CANCELLED)).first()
    if active_project is None:
        active_project = projects.first()
    next_action = _client_next_action(projects, invoices, conversations, sites, active_project)
    pending_approval_count = ProjectStage.objects.filter(
        project__in=projects,
        approval_required=True,
        approval_state=ProjectStage.ApprovalState.PENDING,
    ).count()
    return render(
        request,
        "client_portal/dashboard.html",
        {
            "conversations": conversations[:4],
            "invoices": invoices[:4],
            "orders": orders[:4],
            "sites": sites[:3],
            "projects": projects[:4],
            "project_count": projects.count(),
            "active_project": active_project,
            "profile": _profile(request.user),
            "open_invoices": open_invoices,
            "conversation_count": conversation_count,
            "order_count": order_count,
            "site_count": site_count,
            "pending_approval_count": pending_approval_count,
            "project_summary": _project_dashboard_summary(active_project),
            "next_action": next_action,
        },
    )


@client_required
def projects(request):
    items = _client_projects(request.user).prefetch_related("stages", "sites", "invoices")
    status = request.GET.get("status", "").strip()
    if status in Project.Status.values:
        items = items.filter(status=status)
    return render(
        request,
        "client_portal/projects.html",
        {"projects": items, "statuses": Project.Status.choices, "selected_status": status},
    )


@client_required
def project_detail(request, token):
    project = get_object_or_404(
        _client_projects(request.user)
        .select_related("manager")
        .prefetch_related(
            "stages",
            Prefetch(
                "stages__result_files",
                queryset=ProjectFile.objects.filter(client_visible=True),
                to_attr="visible_result_files",
            ),
            "events",
            "files",
            "conversations__messages",
            "orders",
            "invoices__payments",
            "sites",
        ),
        public_id=token,
    )
    upload_form = ProjectFileUploadForm(project=project, allow_visibility=False, allow_stage=False)
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action in {"approve_stage", "request_stage_changes"}:
            _handle_stage_decision(request, project.stages.all())
            return redirect(f"{reverse('client_portal:project-detail', args=(project.public_id,))}#approvals")
        if action == "upload_file":
            if rate_limited(request, "client-project-upload", limit=10, window=3600):
                messages.error(request, "Слишком много загрузок за короткое время. Попробуйте позже.")
                return redirect("client_portal:project-detail", token=project.public_id)
            upload_form = ProjectFileUploadForm(
                request.POST,
                request.FILES,
                project=project,
                allow_visibility=False,
                allow_stage=False,
            )
            if upload_form.is_valid():
                project_file = upload_form.save(commit=False)
                project_file.project = project
                project_file.uploaded_by = request.user
                project_file.client_visible = True
                project_file.save()
                record_project_event(
                    project,
                    f"Добавлен файл «{project_file.title}»",
                    kind=ProjectEvent.Kind.FILE,
                    actor=request.user,
                )
                messages.success(request, "Файл добавлен в проект.")
                return redirect("client_portal:project-detail", token=project.public_id)
    project.events.filter(client_visible=True, client_seen_at__isnull=True).update(client_seen_at=timezone.now())
    return render(
        request,
        "client_portal/project_detail.html",
        {
            "project": project,
            "upload_form": upload_form,
            "stages": project.stages.all(),
            "events": project.events.filter(client_visible=True)[:50],
            "pending_approvals": project.stages.filter(
                approval_required=True,
                approval_state=ProjectStage.ApprovalState.PENDING,
            ),
            "project_action": _client_next_action(
                Project.objects.filter(pk=project.pk),
                project.invoices.all(),
                project.conversations.all(),
                project.sites.all(),
                project,
            ),
            "files": project.files.filter(client_visible=True),
            "conversations": project.conversations.all(),
            "orders": project.orders.all(),
            "invoices": project.invoices.all(),
            "sites": project.sites.all(),
        },
    )


@client_required
def approvals(request):
    projects = _client_projects(request.user)
    stages = (
        ProjectStage.objects.filter(project__in=projects, approval_required=True)
        .select_related("project")
        .prefetch_related(
            Prefetch(
                "result_files",
                queryset=ProjectFile.objects.filter(client_visible=True),
                to_attr="visible_result_files",
            )
        )
    )
    if request.method == "POST":
        _handle_stage_decision(request, stages)
        return redirect("client_portal:approvals")

    return render(
        request,
        "client_portal/approvals.html",
        {
            "pending_approvals": stages.filter(approval_state=ProjectStage.ApprovalState.PENDING).order_by(
                "due_at", "sort_order"
            ),
            "approval_history": stages.exclude(approval_state=ProjectStage.ApprovalState.PENDING)
            .exclude(approval_state=ProjectStage.ApprovalState.NOT_REQUIRED)
            .order_by("-approval_decided_at", "-updated_at")[:40],
        },
    )


@client_required
def profile(request):
    client_profile = _profile(request.user)
    if not client_profile.telegram_linked:
        client_profile.ensure_telegram_link_code()
    if request.method == "POST":
        action = request.POST.get("action", "save_profile")
        if action == "refresh_telegram_code":
            client_profile.refresh_telegram_link_code()
            client_profile.save(update_fields=("telegram_link_code", "telegram_link_code_created_at"))
            messages.success(request, "Код привязки Telegram обновлен.")
            return redirect("client_portal:profile")
        if action == "disconnect_telegram":
            client_profile.telegram_chat_id = ""
            client_profile.telegram_notifications_enabled = False
            client_profile.refresh_telegram_link_code()
            client_profile.save(
                update_fields=(
                    "telegram_chat_id",
                    "telegram_notifications_enabled",
                    "telegram_link_code",
                    "telegram_link_code_created_at",
                )
            )
            messages.success(request, "Telegram отключен от кабинета.")
            return redirect("client_portal:profile")
        form = ClientRequisitesForm(request.POST, instance=client_profile)
        if form.is_valid():
            client_profile = form.save()
            if client_profile.personal_data_consent:
                record_legal_acceptance(
                    document_type=ConsentAcceptance.DocumentType.PRIVACY,
                    subject=request.user,
                    channel=ConsentAcceptance.Channel.ACCOUNT,
                    request=request,
                    user=request.user,
                )
            messages.success(request, "Данные сохранены.")
            return redirect("client_portal:profile")
    else:
        form = ClientRequisitesForm(instance=client_profile)
    return render(
        request,
        "client_portal/profile.html",
        {"form": form, "profile": client_profile},
    )


@client_required
def create_request(request):
    selected_template = _selected_template(request.POST.get("template_slug") or request.GET.get("template"))
    if request.method == "POST":
        form = ClientLeadForm(request.POST)
        if form.is_valid():
            metadata = _template_metadata(selected_template, request)
            task = form.cleaned_data["task"]
            if selected_template:
                demo_url = metadata["selected_template"]["demo_url"]
                task = f"Выбран шаблон: {selected_template.title}\nДемо: {demo_url}\n\n{task}"
            lead = Lead.objects.create(
                user=request.user,
                name=request.user.get_full_name() or request.user.username,
                email=request.user.email,
                subject=form.cleaned_data["subject"],
                service_type=form.cleaned_data["service_type"],
                task=task,
                source=Lead.Source.FORM,
                page_title=selected_template.title if selected_template else "",
                metadata=metadata,
            )
            conversation = Conversation.objects.create(
                lead=lead,
                user=request.user,
                client_email=request.user.email,
                title=lead.subject,
                status=Conversation.Status.WAITING_MANAGER,
            )
            Message.objects.create(
                conversation=conversation,
                author=request.user,
                author_role=Message.AuthorRole.CLIENT,
                body=lead.task,
            )
            telegram = TelegramBotClient()
            telegram.notify_new_lead(lead, conversation)
            telegram.notify_client_request_created(lead, conversation)
            messages.success(request, "Заявка принята. Скоро менеджер ответит.")
            target_url = reverse("client_portal:conversation-detail", kwargs={"token": conversation.public_token})
            if request.POST.get("mobile_scroll"):
                target_url = f"{target_url}#client-content"
            return redirect(target_url)
    else:
        form = ClientLeadForm(initial=_template_initial(selected_template))
    return render(
        request,
        "client_portal/create_request.html",
        {"form": form, "selected_template": selected_template},
    )


@client_required
def conversations(request):
    items = _client_conversations(request.user).prefetch_related("messages")
    return render(request, "client_portal/conversations.html", {"conversations": items})


@client_required
def orders(request):
    items = _client_orders(request.user).select_related("invoice", "lead").prefetch_related("managed_sites")
    return render(request, "client_portal/orders.html", {"orders": items})


@client_required
def order_detail(request, pk):
    order = get_object_or_404(
        _client_orders(request.user).select_related("invoice", "lead").prefetch_related("managed_sites"),
        pk=pk,
    )
    return render(request, "client_portal/order_detail.html", {"order": order, "sites": order.managed_sites.all()})


@client_required
def conversation_detail(request, token):
    conversation = get_object_or_404(_client_conversations(request.user), public_token=token)
    visible_messages = conversation.messages.filter(is_internal=False)
    _bind_conversation(conversation, request.user)
    selected_template = None
    if conversation.lead_id:
        selected_template = conversation.lead.metadata.get("selected_template")
    if request.method == "POST":
        form = ClientMessageForm(request.POST)
        if form.is_valid():
            message = Message.objects.create(
                conversation=conversation,
                author=request.user,
                author_role=Message.AuthorRole.CLIENT,
                body=form.cleaned_data["body"],
            )
            conversation.status = Conversation.Status.WAITING_MANAGER
            conversation.save(update_fields=("status", "updated_at"))
            TelegramBotClient().notify_client_message(conversation, message)
            return redirect("client_portal:conversation-detail", token=conversation.public_token)
    else:
        form = ClientMessageForm()
    Conversation.objects.filter(pk=conversation.pk).update(client_last_read_at=timezone.now())
    return render(
        request,
        "client_portal/conversation_detail.html",
        {
            "conversation": conversation,
            "visible_messages": visible_messages,
            "form": form,
            "selected_template": selected_template,
        },
    )


@client_required
def invoices(request):
    items = _client_invoices(request.user).prefetch_related("payments")
    return render(request, "client_portal/invoices.html", {"invoices": items})


@client_required
def invoice_detail(request, token):
    invoice = get_object_or_404(_client_invoices(request.user).prefetch_related("payments"), public_token=token)
    return render(request, "client_portal/invoice_detail.html", {"invoice": invoice})


@client_required
def sites(request):
    items = _client_sites(request.user).select_related("order")
    return render(request, "client_portal/sites.html", {"sites": items})


@client_required
def site_detail(request, pk):
    site = get_object_or_404(_client_sites(request.user).select_related("order", "lead"), pk=pk)
    return render(request, "client_portal/site_detail.html", {"site": site})
