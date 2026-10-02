from django.core import signing

from .models import Profile

TELEGRAM_REGISTRATION_SALT = "aurumweb.telegram.registration"
TELEGRAM_REGISTRATION_MAX_AGE = 24 * 60 * 60


def make_telegram_registration_token(chat_id, username="", first_name="", last_name=""):
    payload = {
        "chat_id": str(chat_id or "").strip(),
        "username": str(username or "").strip().lstrip("@"),
        "first_name": str(first_name or "").strip(),
        "last_name": str(last_name or "").strip(),
    }
    return signing.dumps(payload, salt=TELEGRAM_REGISTRATION_SALT, compress=True)


def load_telegram_registration_token(token, max_age=TELEGRAM_REGISTRATION_MAX_AGE):
    token = str(token or "").strip()
    if not token:
        return {}
    try:
        payload = signing.loads(token, salt=TELEGRAM_REGISTRATION_SALT, max_age=max_age)
    except signing.BadSignature:
        return {}
    if not str(payload.get("chat_id") or "").strip():
        return {}
    return payload


def suggested_username(payload):
    username = str(payload.get("username") or "").strip().lstrip("@")
    if username:
        return username[:150]
    first_name = str(payload.get("first_name") or "").strip()
    last_name = str(payload.get("last_name") or "").strip()
    name = "_".join(part for part in (first_name, last_name) if part)
    return name[:150]


def link_profile_to_telegram(profile, payload):
    chat_id = str(payload.get("chat_id") or "").strip()
    if not chat_id:
        return False, "chat_missing"

    if Profile.objects.filter(telegram_chat_id=chat_id).exclude(pk=profile.pk).exists():
        return False, "chat_already_linked"

    username = str(payload.get("username") or "").strip().lstrip("@")
    update_fields = [
        "telegram_chat_id",
        "telegram_notifications_enabled",
        "telegram_link_code",
        "telegram_link_code_created_at",
    ]
    profile.telegram_chat_id = chat_id
    profile.telegram_notifications_enabled = True
    profile.telegram_link_code = ""
    profile.telegram_link_code_created_at = None
    if username:
        profile.telegram_username = f"@{username}"
        update_fields.append("telegram_username")
    profile.save(update_fields=update_fields)
    return True, "linked"
