from apps.accounts.models import Profile
from apps.integrations.telegram import TelegramBotClient


def client_portal(request):
    user = getattr(request, "user", None)
    path = getattr(request, "path", "")
    if not user or not user.is_authenticated or not path.startswith("/client/"):
        return {}
    if user.is_staff or user.is_superuser:
        return {}

    profile, _created = Profile.objects.get_or_create(user=user)
    if not profile.telegram_linked:
        profile.ensure_telegram_link_code()

    telegram = TelegramBotClient()
    username = str(telegram.bot_username or "").strip().removeprefix("@")
    telegram_bot_url = f"https://t.me/{username}" if username else ""
    return {
        "client_profile": profile,
        "client_telegram_bot_url": telegram_bot_url,
    }
