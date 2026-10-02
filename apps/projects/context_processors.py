from django.db.models import F, Q

from apps.messaging.models import Conversation, Message

from .models import Project, ProjectEvent, ProjectStage


def _office_project_attention_count():
    return (
        ProjectEvent.objects.filter(manager_seen_at__isnull=True)
        .exclude(kind=ProjectEvent.Kind.MESSAGE)
        .exclude(project__status__in=(Project.Status.COMPLETED, Project.Status.CANCELLED))
        .filter(Q(actor__isnull=True) | Q(actor__is_staff=False, actor__is_superuser=False))
        .values("project_id")
        .distinct()
        .count()
    )


def _office_conversation_attention_count():
    return (
        Message.objects.filter(
            author_role=Message.AuthorRole.CLIENT,
            is_internal=False,
            conversation__status=Conversation.Status.WAITING_MANAGER,
        )
        .filter(
            Q(conversation__manager_last_read_at__isnull=True)
            | Q(created_at__gt=F("conversation__manager_last_read_at"))
        )
        .values("conversation_id")
        .distinct()
        .count()
    )


def workspace_notifications(request):
    user = getattr(request, "user", None)
    path = getattr(request, "path", "")
    if not user or not user.is_authenticated:
        return {}

    if path.startswith("/office/") and (user.is_staff or user.is_superuser):
        return {
            "office_unread_messages": _office_conversation_attention_count(),
            "office_unread_events": _office_project_attention_count(),
        }

    if path.startswith("/client/") and not user.is_staff and not user.is_superuser:
        email = (user.email or "").strip()
        conversation_query = Q(conversation__user=user)
        project_query = Q(project__user=user)
        if email:
            conversation_query |= Q(conversation__client_email__iexact=email)
            project_query |= Q(project__client_email__iexact=email)
        unread_messages = (
            Message.objects.exclude(author_role=Message.AuthorRole.CLIENT)
            .filter(
                conversation_query,
                is_internal=False,
            )
            .filter(
                Q(conversation__client_last_read_at__isnull=True)
                | Q(created_at__gt=F("conversation__client_last_read_at"))
            )
        )
        return {
            "client_unread_messages": unread_messages.distinct().count(),
            "client_unread_events": ProjectEvent.objects.filter(
                project_query, client_visible=True, client_seen_at__isnull=True
            )
            .distinct()
            .count(),
            "client_pending_approvals": ProjectStage.objects.filter(
                project_query,
                approval_required=True,
                approval_state=ProjectStage.ApprovalState.PENDING,
            )
            .distinct()
            .count(),
        }
    return {}
