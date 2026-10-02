import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.core.rate_limit import rate_limited
from apps.core.security import cross_origin_error, same_origin_or_no_origin
from apps.integrations.email import EmailNotificationClient
from apps.integrations.telegram import TelegramBotClient

from .forms import PublicMessageForm
from .models import Conversation, Message
from .services import AUTO_REPLY_TEXT, add_public_auto_reply

TOO_MANY_REQUESTS = "Слишком много сообщений. Попробуйте написать чуть позже."
CONSENT_REQUIRED = "Подтвердите согласие на обработку персональных данных."
PAYLOAD_TOO_LARGE = "Слишком большой запрос. Сократите текст и попробуйте снова."
MAX_REQUEST_BYTES = 32 * 1024
MAX_MESSAGE_LENGTH = 4000


def _request_too_large(request):
    try:
        return int(request.META.get("CONTENT_LENGTH") or 0) > MAX_REQUEST_BYTES
    except ValueError:
        return False


def _reject_if_long(value, limit, label):
    if len(str(value or "")) > limit:
        return f"{label}: слишком длинное значение."
    return ""


def _noindex(response):
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


def _has_consent(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on", "да", "согласен"}


def _public_author(request):
    if request.user.is_authenticated and not request.user.is_staff:
        return request.user
    return None


@login_required
def inbox(request):
    conversations = Conversation.objects.filter(user=request.user) | Conversation.objects.filter(
        client_email=request.user.email
    )
    return render(request, "messaging/inbox.html", {"conversations": conversations.distinct()})


def public_conversation(request, token):
    conversation = get_object_or_404(Conversation, public_token=token)
    if not conversation.public_access_available:
        return _noindex(HttpResponse("Срок действия ссылки на диалог истек.", status=410))
    visible_messages = conversation.messages.filter(is_internal=False)
    selected_template = None
    if conversation.lead_id:
        selected_template = conversation.lead.metadata.get("selected_template")
    if request.method == "POST":
        form = PublicMessageForm(request.POST)
        if not conversation.public_write_available:
            form.add_error(None, "Диалог закрыт. Для продолжения напишите через личный кабинет.")
            return _noindex(
                render(
                    request,
                    "messaging/conversation.html",
                    {
                        "conversation": conversation,
                        "visible_messages": visible_messages,
                        "form": form,
                        "selected_template": selected_template,
                    },
                    status=409,
                )
            )
        if rate_limited(request, "public-conversation", limit=12, window=300):
            form.add_error(None, TOO_MANY_REQUESTS)
            return _noindex(
                render(
                    request,
                    "messaging/conversation.html",
                    {
                        "conversation": conversation,
                        "visible_messages": visible_messages,
                        "form": form,
                        "selected_template": selected_template,
                    },
                    status=429,
                )
            )
        if form.is_valid():
            message = Message.objects.create(
                conversation=conversation,
                author=_public_author(request),
                author_role=Message.AuthorRole.CLIENT,
                body=form.cleaned_data["body"],
            )
            conversation.status = Conversation.Status.WAITING_MANAGER
            conversation.save(update_fields=("status", "updated_at"))
            EmailNotificationClient().notify_client_message(conversation, message)
            TelegramBotClient().notify_client_message(conversation, message)
            return redirect("messenger:public-conversation", token=conversation.public_token)
    else:
        form = PublicMessageForm()
    return _noindex(
        render(
            request,
            "messaging/conversation.html",
            {
                "conversation": conversation,
                "visible_messages": visible_messages,
                "form": form,
                "selected_template": selected_template,
            },
        )
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
def create_message(request):
    if not same_origin_or_no_origin(request):
        return JsonResponse(cross_origin_error(), status=403)
    if _request_too_large(request):
        return JsonResponse({"ok": False, "error": PAYLOAD_TOO_LARGE}, status=413)
    if rate_limited(request, "message-api", limit=20, window=300):
        return JsonResponse({"ok": False, "error": TOO_MANY_REQUESTS}, status=429)

    data = _payload(request)
    if not _has_consent(data.get("personal_data_consent")):
        return JsonResponse({"ok": False, "error": CONSENT_REQUIRED}, status=400)
    token = data.get("conversation_token")
    body = data.get("message") or data.get("body") or ""
    if not body.strip():
        return JsonResponse({"ok": False, "error": "Сообщение пустое"}, status=400)
    for error in (
        _reject_if_long(body, MAX_MESSAGE_LENGTH, "Сообщение"),
        _reject_if_long(data.get("title", ""), 220, "Тема"),
        _reject_if_long(data.get("email", ""), 254, "Email"),
    ):
        if error:
            return JsonResponse({"ok": False, "error": error}, status=400)

    conversation = None
    created_conversation = False
    if token:
        try:
            conversation = Conversation.objects.filter(public_token=token).first()
        except ValidationError:
            return JsonResponse({"ok": False, "error": "Некорректный токен диалога."}, status=400)

    if conversation is not None and not conversation.public_write_available:
        return JsonResponse({"ok": False, "error": "Ссылка на диалог недействительна или диалог закрыт."}, status=403)

    if conversation is None:
        conversation = Conversation.objects.create(
            user=_public_author(request),
            client_email=data.get("email", ""),
            title=data.get("title", "Диалог с сайта"),
        )
        created_conversation = True

    message = Message.objects.create(
        conversation=conversation,
        author=_public_author(request),
        author_role=Message.AuthorRole.CLIENT,
        body=body,
    )
    if created_conversation:
        add_public_auto_reply(conversation)
    conversation.status = Conversation.Status.WAITING_MANAGER
    conversation.save(update_fields=("status", "updated_at"))
    EmailNotificationClient().notify_client_message(conversation, message)
    TelegramBotClient().notify_client_message(conversation, message)
    return JsonResponse(
        {
            "ok": True,
            "message_id": message.id,
            "conversation_token": str(conversation.public_token),
            "auto_reply": AUTO_REPLY_TEXT if created_conversation else "",
        }
    )
