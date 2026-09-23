from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.forms import ClientRequisitesForm
from accounts.models import Profile
from content.public_data import find_template

from .forms import ClientLeadForm
from .models import ClientRequest


def _profile(user):
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


def _project_action(project):
    if project.status == ClientRequest.Status.COMPLETED:
        return {
            "title": "Работа завершена",
            "text": "Результат по задаче готов.",
            "url": reverse("client_portal:create-request"),
            "label": "Создать новую задачу",
            "kind": "completed",
        }
    if project.status == ClientRequest.Status.IN_PROGRESS:
        return {
            "title": "Задача в работе",
            "text": "Информация о следующих шагах будет появляться на странице проекта.",
            "url": reverse("client_portal:project-detail", args=(project.public_id,)),
            "label": "Открыть проект",
            "kind": "project",
        }
    return {
        "title": "Задача отправлена",
        "text": "Менеджер ознакомится с описанием и уточнит детали.",
        "url": reverse("client_portal:project-detail", args=(project.public_id,)),
        "label": "Открыть задачу",
        "kind": "request",
    }


@login_required
def dashboard(request):
    projects = ClientRequest.objects.filter(client=request.user)
    active_project = projects.first()
    if active_project:
        project_summary = {
            "headline": active_project.title,
            "detail": active_project.summary,
            "steps": [],
        }
        next_action = _project_action(active_project)
    else:
        project_summary = {
            "headline": "Создайте первую задачу",
            "detail": "После заявки здесь появятся этапы, сроки, сообщения и оплаты.",
            "steps": [],
        }
        next_action = {
            "title": "Создайте первую задачу",
            "text": "Опишите сайт, поддержку, Python-программу или Telegram-бота, и менеджер откроет рабочий диалог.",
            "url": reverse("client_portal:create-request"),
            "label": "Создать новую задачу",
            "kind": "request",
        }

    return render(
        request,
        "client_portal/dashboard.html",
        {
            "conversations": (),
            "invoices": (),
            "orders": (),
            "sites": (),
            "projects": projects[:5],
            "project_count": projects.count(),
            "active_project": active_project,
            "profile": _profile(request.user),
            "open_invoices": 0,
            "conversation_count": 0,
            "order_count": 0,
            "site_count": 0,
            "pending_approval_count": 0,
            "project_summary": project_summary,
            "next_action": next_action,
        },
    )


@login_required
def profile(request):
    client_profile = _profile(request.user)
    if request.method == "POST" and request.POST.get("action") == "save_profile":
        form = ClientRequisitesForm(request.POST, instance=client_profile)
        if form.is_valid():
            form.save()
            messages.success(request, "Данные сохранены.")
            return redirect("client_portal:profile")
    else:
        if request.method == "POST" and request.POST.get("action") == "disconnect_telegram":
            client_profile.telegram_chat_id = ""
            client_profile.telegram_notifications_enabled = False
            client_profile.save(update_fields=("telegram_chat_id", "telegram_notifications_enabled"))
            messages.success(request, "Telegram отключен.")
            return redirect("client_portal:profile")
        form = ClientRequisitesForm(instance=client_profile)

    return render(
        request,
        "client_portal/profile.html",
        {
            "form": form,
            "profile": client_profile,
            "client_telegram_bot_url": "",
        },
    )


@login_required
def telegram_connect(request):
    messages.info(request, "Подключение Telegram будет доступно после настройки бота.")
    return redirect("client_portal:profile")


@login_required
def create_request(request):
    template_slug = request.POST.get("template_slug") or request.GET.get("template") or ""
    selected_template = find_template(template_slug)
    initial = {"template_slug": template_slug}
    if selected_template:
        initial.update(
            {
                "service_type": "Сайт на базе шаблона",
                "subject": f"Сайт на базе примера «{selected_template.title}»",
            }
        )
    form = ClientLeadForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        ClientRequest.objects.create(
            client=request.user,
            title=form.cleaned_data["subject"],
            service_type=form.cleaned_data["service_type"],
            summary=form.cleaned_data["task"],
            template_slug=form.cleaned_data["template_slug"],
        )
        messages.success(request, "Задача отправлена.")
        return redirect("client_portal:dashboard")

    return render(
        request,
        "client_portal/create_request.html",
        {"form": form, "selected_template": selected_template},
    )


@login_required
def projects(request):
    selected_status = request.GET.get("status", "")
    queryset = ClientRequest.objects.filter(client=request.user)
    if selected_status in ClientRequest.Status.values:
        queryset = queryset.filter(status=selected_status)
    return render(
        request,
        "client_portal/projects.html",
        {
            "projects": queryset,
            "statuses": ClientRequest.Status.choices,
            "selected_status": selected_status,
        },
    )


@login_required
def project_detail(request, token):
    project = get_object_or_404(ClientRequest, client=request.user, public_id=token)
    return render(
        request,
        "client_portal/project_detail.html",
        {
            "project": project,
            "project_action": _project_action(project),
            "stages": (),
            "pending_approvals": (),
            "files": (),
            "upload_form": None,
            "conversations": (),
            "invoices": (),
            "sites": (),
            "events": (),
        },
    )


@login_required
def approvals(request):
    return render(request, "client_portal/approvals.html", {"pending_approvals": (), "approval_history": ()})


@login_required
def conversations(request):
    return render(request, "client_portal/conversations.html", {"conversations": ()})


@login_required
def orders(request):
    return render(request, "client_portal/orders.html", {"orders": ()})


@login_required
def invoices(request):
    return render(request, "client_portal/invoices.html", {"invoices": ()})


@login_required
def sites(request):
    return render(request, "client_portal/sites.html", {"sites": ()})


@login_required
def unavailable_detail(request, *args, **kwargs):
    raise Http404("Страница не найдена.")
