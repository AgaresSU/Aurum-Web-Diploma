from functools import partial, wraps
from urllib.parse import quote, urlencode

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required as django_staff_member_required
from django.contrib.auth.models import User
from django.shortcuts import redirect
from django.utils import timezone

from apps.accounts.access import needs_totp_setup
from apps.accounts.models import Profile

base_staff_member_required = partial(django_staff_member_required, login_url="/accounts/login/")


def staff_member_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if needs_totp_setup(request.user):
            messages.warning(request, "Перед работой в админке включите TOTP и сохраните резервные коды.")
            return redirect("office:security")
        return view_func(request, *args, **kwargs)

    return base_staff_member_required(wrapped)


CHECKLIST_FIELDS = (
    "has_domain",
    "has_hosting",
    "needs_design",
    "needs_content",
    "needs_seo",
    "needs_robokassa",
    "needs_telegram",
    "needs_client_portal",
    "needs_billing",
    "needs_admin",
    "needs_support",
)


def lead_metadata(lead):
    return dict(lead.metadata or {})


def get_profile(user):
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


def find_profile(user):
    if not user:
        return None
    return Profile.objects.filter(user=user).first()


def conversation_email(conversation):
    if conversation.client_email:
        return conversation.client_email
    if conversation.lead and conversation.lead.email:
        return conversation.lead.email
    if conversation.user and conversation.user.email:
        return conversation.user.email
    return ""


def _is_client_user(user, email):
    if not user or user.is_staff or user.is_superuser:
        return False
    profile = find_profile(user)
    if profile and profile.role != Profile.Role.CLIENT:
        return False
    return not (email and user.email and user.email.lower() != email.lower())


def conversation_user(conversation, email):
    if conversation.user_id and _is_client_user(conversation.user, email):
        return conversation.user
    if conversation.lead and conversation.lead.user_id and _is_client_user(conversation.lead.user, email):
        return conversation.lead.user
    if email:
        user = User.objects.filter(email__iexact=email).first()
        if _is_client_user(user, email):
            return user
    return None


def build_otpauth_uri(user, secret):
    issuer = "AurumWeb"
    label = f"{issuer}:{user.email or user.username}"
    return (
        "otpauth://totp/"
        + quote(label)
        + "?"
        + urlencode(
            {
                "secret": secret,
                "issuer": issuer,
                "algorithm": "SHA1",
                "digits": 6,
                "period": 30,
            }
        )
    )


def _inferred_bool(qualification, integrations, key, markers):
    if key in qualification:
        return qualification.get(key)
    return any(marker in integrations for marker in markers)


def qualification_initial(lead):
    metadata = lead_metadata(lead)
    brief = metadata.get("public_brief", {})
    selected_template = metadata.get("selected_template", {})
    qualification = metadata.get("qualification", {})
    integrations = qualification.get("integrations") or brief.get("integrations") or []
    return {
        "project_type": qualification.get("project_type") or brief.get("site_type") or lead.service_type,
        "industry": qualification.get("industry") or brief.get("industry") or selected_template.get("industry", ""),
        "goal": qualification.get("goal") or brief.get("goal") or selected_template.get("conversion_focus", ""),
        "budget": qualification.get("budget") or brief.get("budget", ""),
        "timeframe": qualification.get("timeframe") or brief.get("timeframe", ""),
        "contact": qualification.get("contact") or brief.get("contact", ""),
        "integrations": integrations,
        "has_domain": qualification.get("has_domain", False),
        "has_hosting": qualification.get("has_hosting", False),
        "needs_design": qualification.get("needs_design", bool(selected_template)),
        "needs_content": qualification.get("needs_content", True),
        "needs_seo": _inferred_bool(qualification, integrations, "needs_seo", ("SEO-страницы",)),
        "needs_robokassa": _inferred_bool(qualification, integrations, "needs_robokassa", ("Robokassa",)),
        "needs_telegram": _inferred_bool(qualification, integrations, "needs_telegram", ("Telegram-бот",)),
        "needs_client_portal": _inferred_bool(qualification, integrations, "needs_client_portal", ("Личный кабинет",)),
        "needs_billing": _inferred_bool(qualification, integrations, "needs_billing", ("Счета и заказы",)),
        "needs_admin": _inferred_bool(qualification, integrations, "needs_admin", ("CRM / админка",)),
        "needs_support": qualification.get("needs_support", True),
        "manager_notes": qualification.get("manager_notes", ""),
    }


def qualification_payload(cleaned_data):
    payload = {}
    for key, value in cleaned_data.items():
        if key == "integrations":
            payload[key] = list(value)
        else:
            payload[key] = value
    return payload


def build_proposal_draft(lead, qualification, selected_template, user):
    title = lead.subject or lead.service_type or f"Предложение по заявке #{lead.pk}"
    scope = [
        "Аналитика задачи, уточнение структуры и сценариев заявок.",
        "Адаптация выбранного шаблона или сборка индивидуальной структуры страниц.",
        "Сборка сайта с формами, базовой аналитикой и подготовкой к сопровождению.",
    ]
    if qualification.get("needs_content"):
        scope.append("Подготовка и укладка контента: офферы, блоки, тексты, FAQ.")
    if qualification.get("needs_seo"):
        scope.append("SEO-архитектура: категории, посадочные страницы, мета-структура.")
    if qualification.get("needs_admin"):
        scope.append("CRM/админка для заявок, статусов и управляемого контента.")
    if qualification.get("needs_client_portal"):
        scope.append("Личный кабинет клиента с диалогом, счетами и статусом работ.")
    if qualification.get("needs_billing") or qualification.get("needs_robokassa"):
        scope.append("Счета, заказы и заготовка подключения Robokassa.")
    if qualification.get("needs_telegram"):
        scope.append("Telegram-уведомления и заготовка бота для менеджера.")
    if qualification.get("needs_support"):
        scope.append("Сопровождение после запуска: правки, SEO-развитие, новые блоки.")

    lines = [
        title,
        "",
        "Исходные данные:",
        f"Тип сайта: {qualification.get('project_type') or '-'}",
        f"Ниша: {qualification.get('industry') or '-'}",
        f"Цель: {qualification.get('goal') or '-'}",
        f"Бюджетный ориентир: {qualification.get('budget') or '-'}",
        f"Сроки: {qualification.get('timeframe') or '-'}",
    ]
    if selected_template:
        lines.append(f"Основа: {selected_template.get('title')} ({selected_template.get('demo_url')})")
    if qualification.get("integrations"):
        lines.append(f"Интеграции: {', '.join(qualification['integrations'])}")
    if qualification.get("manager_notes"):
        lines.extend(["", "Заметки менеджера:", qualification["manager_notes"]])
    lines.extend(["", "Предлагаемый состав работ:"])
    lines.extend(f"- {item}" for item in scope)
    lines.extend(
        [
            "",
            "Следующий шаг:",
            "Согласовать состав работ, уточнить стоимость в диалоге и выставить счет.",
        ]
    )
    return {
        "title": title,
        "body": "\n".join(lines),
        "scope": scope,
        "created_by": user.username,
        "updated_at": timezone.now().isoformat(),
    }


def dashboard_action(title, text, url, label, count=0):
    return {"title": title, "text": text, "url": url, "label": label, "count": count}
