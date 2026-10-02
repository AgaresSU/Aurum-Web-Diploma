import json

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.content.models import TemplateProduct
from apps.content.presentation import clean_template_product
from apps.core.legal_acceptance import record_legal_acceptance
from apps.core.models import ConsentAcceptance
from apps.core.rate_limit import rate_limited
from apps.core.security import cross_origin_error, same_origin_or_no_origin
from apps.integrations.email import EmailNotificationClient
from apps.integrations.telegram import TelegramBotClient
from apps.messaging.models import Conversation, Message
from apps.messaging.services import AUTO_REPLY_TEXT, add_public_auto_reply

from .forms import PublicBriefForm
from .models import Lead

TOO_MANY_REQUESTS = "Слишком много запросов. Попробуйте отправить форму чуть позже."
CONSENT_REQUIRED = "Подтвердите согласие на обработку персональных данных."
PAYLOAD_TOO_LARGE = "Слишком большой запрос. Сократите текст и попробуйте снова."
MAX_REQUEST_BYTES = 64 * 1024
MAX_TASK_LENGTH = 8000
MAX_COMMENT_LENGTH = 5000


def _request_too_large(request):
    try:
        return int(request.META.get("CONTENT_LENGTH") or 0) > MAX_REQUEST_BYTES
    except ValueError:
        return False


def _reject_if_long(value, limit, label):
    if len(str(value or "")) > limit:
        return f"{label}: слишком длинное значение."
    return ""


def _has_consent(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on", "да", "согласен"}


def _website_templates():
    return TemplateProduct.objects.filter(
        is_published=True,
        template_type=TemplateProduct.TemplateType.WEBSITE,
    ).order_by("category", "sort_order", "title")


def _selected_template(slug):
    if not slug:
        return None
    return _website_templates().filter(slug=slug).first()


def _template_for_public_display(template):
    if template is None:
        return None
    return clean_template_product(template)


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
            "demo_url": _template_demo_url(template, request),
        }
    }


def _template_demo_url(template, request):
    return request.build_absolute_uri(reverse("template-site-demo", kwargs={"slug": template.slug}))


def _brief_initial(template):
    if not template:
        return {
            "site_type": "Корпоративный сайт",
            "goal": "Получать заявки",
            "budget": "Нужно оценить",
            "timeframe": "Пока планируем",
        }
    comment = [
        f"Интересен пример «{template.title}».",
        "Нужно понять подходящий состав работ, сроки и поддержку после запуска.",
    ]
    return {
        "template_slug": template.slug,
        "site_type": "Каталог услуг",
        "industry": template.industry or template.category,
        "goal": "Получать заявки",
        "budget": "Нужно оценить",
        "timeframe": "Пока планируем",
        "task": "\n".join(comment),
    }


def _build_brief_task(cleaned, template, request):
    integrations = cleaned.get("integrations") or []
    lines = [
        "Заявка с сайта",
        "",
        f"Тип сайта: {cleaned['site_type']}",
        f"Ниша: {cleaned['industry']}",
        f"Главная цель: {cleaned['goal']}",
        f"Бюджетный ориентир: {cleaned['budget']}",
        f"Сроки: {cleaned['timeframe']}",
        f"Интеграции: {', '.join(integrations) if integrations else 'пока не выбраны'}",
    ]
    if template:
        lines.extend(
            [
                "",
                f"Выбранный пример: {template.title}",
                f"Демо: {_template_demo_url(template, request)}",
            ]
        )
    if cleaned.get("contact"):
        lines.append(f"Контакт: {cleaned['contact']}")
    if cleaned.get("task"):
        lines.extend(["", "Комментарий клиента:", cleaned["task"]])
    return "\n".join(lines)


def _email_from_contact(contact):
    for token in (contact or "").replace(",", " ").split():
        candidate = token.strip("<>()[]")
        try:
            validate_email(candidate)
        except ValidationError:
            continue
        return candidate
    return ""


def _notify_lead_created(lead, conversation):
    telegram = TelegramBotClient()
    telegram.notify_new_lead(lead, conversation)
    telegram.notify_client_request_created(lead, conversation)
    EmailNotificationClient().notify_new_lead(lead, conversation)


def _demo_request_fields(data):
    fields = []
    for index in range(1, 12):
        label = (data.get(f"request_field_label_{index}") or "").strip()
        value = (data.get(f"request_field_value_{index}") or data.get(f"field_{index}") or "").strip()
        if label or value:
            fields.append({"label": label or f"Поле {index}", "value": value})
    return fields


def _build_demo_request_task(template, fields, reply_contact, client_name, comment, request):
    demo_url = _template_demo_url(template, request)
    lines = [
        "Быстрый бриф из демо-сайта",
        "",
        f"Шаблон: {template.title}",
        f"Категория: {template.category or '-'}",
        f"Ниша: {template.industry or '-'}",
        f"Демо: {demo_url}",
        "",
        "Ответы клиента:",
    ]
    for field in fields:
        lines.append(f"- {field['label']}: {field['value'] or 'не указано'}")
    if client_name:
        lines.extend(["", f"Имя: {client_name}"])
    if reply_contact:
        lines.append(f"Куда ответить: {reply_contact}")
    if comment:
        lines.extend(["", "Комментарий клиента:", comment])
    return "\n".join(lines)


@csrf_exempt
@require_POST
def create_template_request(request, slug):
    if not same_origin_or_no_origin(request):
        return JsonResponse(cross_origin_error(), status=403)
    if _request_too_large(request):
        return JsonResponse({"ok": False, "error": PAYLOAD_TOO_LARGE}, status=413)
    if rate_limited(request, "template-request", limit=6, window=300):
        return JsonResponse({"ok": False, "error": TOO_MANY_REQUESTS}, status=429)
    if not _has_consent(request.POST.get("personal_data_consent")):
        return JsonResponse({"ok": False, "error": CONSENT_REQUIRED}, status=400)

    template = get_object_or_404(
        TemplateProduct,
        slug=slug,
        is_published=True,
        template_type=TemplateProduct.TemplateType.WEBSITE,
    )
    fields = _demo_request_fields(request.POST)
    client_name = (request.POST.get("client_name") or request.POST.get("name") or "").strip()
    reply_contact = (
        request.POST.get("reply_contact") or request.POST.get("contact") or request.POST.get("email") or ""
    ).strip()
    comment = (request.POST.get("comment") or "").strip()
    for error in (
        _reject_if_long(client_name, 160, "Имя"),
        _reject_if_long(reply_contact, 254, "Контакт"),
        _reject_if_long(comment, MAX_COMMENT_LENGTH, "Комментарий"),
    ):
        if error:
            return JsonResponse({"ok": False, "error": error}, status=400)
    for field in fields:
        for error in (
            _reject_if_long(field["label"], 160, "Название поля"),
            _reject_if_long(field["value"], 1000, "Значение поля"),
        ):
            if error:
                return JsonResponse({"ok": False, "error": error}, status=400)
    task = _build_demo_request_task(template, fields, reply_contact, client_name, comment, request)
    email = _email_from_contact(reply_contact)
    user = request.user if request.user.is_authenticated else None
    display_name = client_name
    if not display_name and user:
        display_name = user.get_full_name() or user.username
    integrations = ["Форма заявки", "Встроенный мессенджер", "CRM / админка", "SEO-страницы"]
    metadata = {
        "public_brief": {
            "site_type": "Сайт на базе шаблона",
            "industry": template.industry or template.category,
            "goal": template.conversion_focus or "Получать заявки",
            "integrations": integrations,
            "budget": "Нужно оценить",
            "timeframe": "Пока планируем",
            "contact": reply_contact,
        },
        "demo_request": {
            "request_title": (request.POST.get("request_title") or "").strip(),
            "fields": fields,
            "client_name": client_name,
            "reply_contact": reply_contact,
            "comment": comment,
            "personal_data_consent": True,
            "source_page": request.META.get("HTTP_REFERER", ""),
        },
    }
    metadata.update(_template_metadata(template, request))

    lead = Lead.objects.create(
        user=user,
        name=display_name,
        email=email or (user.email if user else ""),
        subject=f"Заявка по примеру сайта: {template.title}",
        service_type="Сайт на базе примера",
        task=task,
        page_title=template.title,
        source=Lead.Source.FORM,
        metadata=metadata,
    )
    record_legal_acceptance(
        document_type=ConsentAcceptance.DocumentType.PRIVACY,
        subject=lead,
        channel=ConsentAcceptance.Channel.WEB,
        request=request,
        user=lead.user,
    )
    conversation = Conversation.objects.create(
        lead=lead,
        user=lead.user,
        client_email=lead.email,
        title=lead.subject,
        status=Conversation.Status.WAITING_MANAGER,
    )
    Message.objects.create(
        conversation=conversation,
        author=user,
        author_role=Message.AuthorRole.CLIENT,
        body=task,
    )
    add_public_auto_reply(conversation)
    _notify_lead_created(lead, conversation)
    public_url = reverse("messenger:public-conversation", kwargs={"token": conversation.public_token})
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse(
            {
                "ok": True,
                "lead_id": lead.pk,
                "conversation_token": str(conversation.public_token),
                "redirect_url": public_url,
            },
            status=201,
        )
    return redirect("messenger:public-conversation", token=conversation.public_token)


def public_brief(request):
    raw_templates = list(_website_templates())
    templates = [clean_template_product(template) for template in raw_templates]
    selected_template = _template_for_public_display(
        _selected_template(request.POST.get("template_slug") or request.GET.get("template"))
    )

    if request.method == "POST":
        form = PublicBriefForm(request.POST, templates=templates)
        if rate_limited(request, "public-brief", limit=5, window=300):
            form.add_error(None, TOO_MANY_REQUESTS)
            return render(
                request,
                "leads/public_brief.html",
                {
                    "form": form,
                    "selected_template": selected_template,
                    "template_count": len(templates),
                },
                status=429,
            )
        if form.is_valid():
            selected_template = _template_for_public_display(_selected_template(form.cleaned_data.get("template_slug")))
            task = _build_brief_task(form.cleaned_data, selected_template, request)
            metadata = {
                "public_brief": {
                    "site_type": form.cleaned_data["site_type"],
                    "industry": form.cleaned_data["industry"],
                    "goal": form.cleaned_data["goal"],
                    "integrations": form.cleaned_data.get("integrations") or [],
                    "budget": form.cleaned_data["budget"],
                    "timeframe": form.cleaned_data["timeframe"],
                    "contact": form.cleaned_data.get("contact", ""),
                    "personal_data_consent": True,
                }
            }
            metadata.update(_template_metadata(selected_template, request))
            subject = f"Заявка на сайт: {form.cleaned_data['industry']}"
            if selected_template:
                subject = f"Сайт на базе примера: {selected_template.title}"

            lead = Lead.objects.create(
                user=request.user if request.user.is_authenticated else None,
                name=form.cleaned_data["name"],
                email=form.cleaned_data["email"],
                subject=subject,
                service_type="Заявка с сайта",
                task=task,
                page_title=selected_template.title if selected_template else "Заявка с сайта",
                source=Lead.Source.FORM,
                metadata=metadata,
            )
            record_legal_acceptance(
                document_type=ConsentAcceptance.DocumentType.PRIVACY,
                subject=lead,
                channel=ConsentAcceptance.Channel.WEB,
                request=request,
                user=lead.user,
            )
            conversation = Conversation.objects.create(
                lead=lead,
                user=lead.user,
                client_email=lead.email,
                title=lead.subject,
                status=Conversation.Status.WAITING_MANAGER,
            )
            Message.objects.create(
                conversation=conversation,
                author=request.user if request.user.is_authenticated else None,
                author_role=Message.AuthorRole.CLIENT,
                body=task,
            )
            add_public_auto_reply(conversation)
            _notify_lead_created(lead, conversation)
            return redirect("messenger:public-conversation", token=conversation.public_token)
    else:
        form = PublicBriefForm(
            templates=templates,
            initial=_brief_initial(selected_template),
        )

    return render(
        request,
        "leads/public_brief.html",
        {
            "form": form,
            "selected_template": selected_template,
            "template_count": len(templates),
        },
    )


def _payload(request):
    if request.content_type == "application/json":
        try:
            return json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}
    return request.POST.dict()


@csrf_exempt
@require_POST
def create_lead(request):
    if not same_origin_or_no_origin(request):
        return JsonResponse(cross_origin_error(), status=403)
    if _request_too_large(request):
        return JsonResponse({"ok": False, "error": PAYLOAD_TOO_LARGE}, status=413)
    if rate_limited(request, "lead-api", limit=8, window=300):
        return JsonResponse({"ok": False, "error": TOO_MANY_REQUESTS}, status=429)

    data = _payload(request)
    if not _has_consent(data.get("personal_data_consent")):
        return JsonResponse({"ok": False, "error": CONSENT_REQUIRED}, status=400)
    task = data.get("task") or data.get("message") or data.get("messenger") or ""
    if not task.strip():
        return JsonResponse({"ok": False, "error": "Опишите задачу"}, status=400)
    for error in (
        _reject_if_long(data.get("name", ""), 160, "Имя"),
        _reject_if_long(data.get("email", ""), 254, "Email"),
        _reject_if_long(data.get("subject", ""), 220, "Тема"),
        _reject_if_long(data.get("type", ""), 120, "Тип задачи"),
        _reject_if_long(data.get("page", ""), 220, "Страница"),
        _reject_if_long(task, MAX_TASK_LENGTH, "Описание задачи"),
    ):
        if error:
            return JsonResponse({"ok": False, "error": error}, status=400)

    lead = Lead.objects.create(
        user=request.user if request.user.is_authenticated else None,
        name=data.get("name", ""),
        email=data.get("email", ""),
        subject=data.get("subject", ""),
        service_type=data.get("type", ""),
        task=task,
        page_title=data.get("page", ""),
        source=Lead.Source.MESSENGER if data.get("messenger") else Lead.Source.FORM,
        metadata={key: value for key, value in data.items() if key not in {"name", "email", "subject", "type", "task"}},
    )
    record_legal_acceptance(
        document_type=ConsentAcceptance.DocumentType.PRIVACY,
        subject=lead,
        channel=ConsentAcceptance.Channel.WEB,
        request=request,
        user=lead.user,
    )
    conversation = Conversation.objects.create(
        lead=lead,
        user=lead.user,
        client_email=lead.email,
        title=lead.subject or lead.service_type or "Новая задача",
    )
    Message.objects.create(conversation=conversation, author_role=Message.AuthorRole.CLIENT, body=task)
    add_public_auto_reply(conversation)
    _notify_lead_created(lead, conversation)

    return JsonResponse(
        {
            "ok": True,
            "lead_id": lead.id,
            "conversation_token": str(conversation.public_token),
            "conversation_url": reverse("messenger:public-conversation", kwargs={"token": conversation.public_token}),
            "message": AUTO_REPLY_TEXT,
        },
        status=201,
    )
