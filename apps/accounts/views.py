import hashlib
import logging
import time
from urllib.parse import urlencode, urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME, login, logout, views as auth_views
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.mail import send_mail
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.core.rate_limit import client_ip, rate_limited

from .access import has_totp_configured, is_admin_user, needs_totp_setup, profile_for
from .forms import EmailAuthenticationForm, EmailVerificationForm, RegistrationForm, TotpAuthenticationForm
from .models import EmailVerificationCode
from .security import consume_backup_code
from .telegram_registration import (
    link_profile_to_telegram,
    load_telegram_registration_token,
    suggested_username,
)
from .totp import verify_totp

logger = logging.getLogger(__name__)
TOTP_MAX_ATTEMPTS = 5
TOTP_LOCK_SECONDS = 300
EMAIL_CODE_RESEND_SECONDS = 60
TOO_MANY_AUTH_REQUESTS = "Слишком много попыток. Попробуйте повторить через несколько минут."


def _profile(user):
    return profile_for(user)


def _is_admin_user(user):
    return is_admin_user(user)


def _default_redirect_name(user):
    if needs_totp_setup(user):
        return "office:security"
    return "office:dashboard" if _is_admin_user(user) else "client_portal:dashboard"


def _safe_redirect_url(request, redirect_to, fallback_name, user=None):
    if not redirect_to or not url_has_allowed_host_and_scheme(
        url=redirect_to,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return reverse(fallback_name)

    path = urlparse(redirect_to).path
    targets_admin_area = (
        path == "/office" or path.startswith("/office/") or path == "/admin" or path.startswith("/admin/")
    )
    targets_client_area = path == "/client" or path.startswith("/client/")

    if user is not None:
        if needs_totp_setup(user):
            security_path = reverse("office:security")
            return redirect_to if path == security_path else security_path
        is_admin = _is_admin_user(user)
        if targets_admin_area and not is_admin:
            return reverse("client_portal:dashboard")
        if targets_client_area and is_admin:
            return reverse("office:dashboard")

    return redirect_to


def _requires_totp(user):
    return _is_admin_user(user) and has_totp_configured(user)


def _client_ip(request):
    return client_ip(request)


def _setting_int(name, default):
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


def _rate_identifier(value):
    normalized = str(value or "empty").strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _security_rate_key(request, scope, identifier=""):
    return f"security:{scope}:{_client_ip(request)}:{_rate_identifier(identifier)}"


def _security_lock_remaining(key):
    status = cache.get(key) or {}
    locked_until = float(status.get("locked_until") or 0)
    remaining = int(locked_until - time.time())
    if remaining > 0:
        return remaining
    if locked_until:
        cache.delete(key)
    return 0


def _record_security_failure(key, max_attempts, lock_seconds):
    status = cache.get(key) or {}
    attempts = int(status.get("attempts") or 0) + 1
    if attempts >= max_attempts:
        cache.set(key, {"attempts": 0, "locked_until": time.time() + lock_seconds}, timeout=lock_seconds)
        return lock_seconds
    cache.set(key, {"attempts": attempts, "locked_until": 0}, timeout=lock_seconds)
    return 0


def _clear_security_rate(key):
    cache.delete(key)


def _verification_session_set(request, user, redirect_to=""):
    request.session["pending_email_user_id"] = user.pk
    request.session["pending_email_next"] = redirect_to


def _verification_session_clear(request):
    request.session.pop("pending_email_user_id", None)
    request.session.pop("pending_email_next", None)


def _pending_verification_user(request):
    user_id = request.session.get("pending_email_user_id")
    if not user_id:
        return None
    return User.objects.filter(pk=user_id).first()


def _site_url(request):
    configured_url = getattr(settings, "AURUMWEB_SITE_URL", "").strip()
    if configured_url:
        return configured_url.rstrip("/")
    return request.build_absolute_uri("/").rstrip("/")


def _send_email_verification_code(request, user):
    verification_code, code = EmailVerificationCode.issue(
        user=user,
        purpose=EmailVerificationCode.Purpose.EMAIL_VERIFY,
        email=user.email,
        ttl_minutes=getattr(settings, "AURUMWEB_EMAIL_CODE_TTL_MINUTES", 20),
    )
    context = {
        "user": user,
        "code": code,
        "expires_at": verification_code.expires_at,
        "site_url": _site_url(request),
    }
    subject = f"{settings.AURUMWEB_EMAIL_SUBJECT_PREFIX}Код подтверждения email".strip()
    message = render_to_string("accounts/email_verification_email.txt", context)
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)
    return verification_code


def _send_email_verification_code_safely(request, user):
    try:
        _send_email_verification_code(request, user)
        return True
    except Exception:
        logger.exception("Email verification code delivery failed for user_id=%s", user.pk)
        messages.error(
            request,
            "Аккаунт создан, но письмо сейчас не отправилось. Проверьте SMTP-настройки или запросите код позже.",
        )
        return False


def _has_active_email_code(user):
    return EmailVerificationCode.objects.filter(
        user=user,
        purpose=EmailVerificationCode.Purpose.EMAIL_VERIFY,
        used_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).exists()


def _totp_rate_key(user):
    return f"totp-attempts:{user.pk}"


def _totp_lock_remaining(request, user):
    status = cache.get(_totp_rate_key(user)) or {}
    locked_until = float(status.get("locked_until") or 0)
    remaining = int(locked_until - time.time())
    if remaining > 0:
        return remaining
    if locked_until:
        cache.delete(_totp_rate_key(user))
    return 0


def _record_totp_failure(request, user):
    key = _totp_rate_key(user)
    status = cache.get(key) or {}
    attempts = int(status.get("attempts") or 0) + 1
    if attempts >= TOTP_MAX_ATTEMPTS:
        cache.set(key, {"attempts": 0, "locked_until": time.time() + TOTP_LOCK_SECONDS}, timeout=TOTP_LOCK_SECONDS)
        return TOTP_LOCK_SECONDS
    cache.set(key, {"attempts": attempts, "locked_until": 0}, timeout=TOTP_LOCK_SECONDS)
    return 0


def _clear_totp_rate(request, user):
    cache.delete(_totp_rate_key(user))


def _clear_pending_totp(request):
    request.session.pop("pending_totp_user_id", None)
    request.session.pop("pending_totp_next", None)


def _switch_account_confirmation(request, *, target, hidden_fields=None):
    return render(
        request,
        "accounts/switch_account.html",
        {
            "target": target,
            "hidden_fields": hidden_fields or {},
            "cancel_url": _safe_redirect_url(
                request,
                request.GET.get(REDIRECT_FIELD_NAME) or "",
                _default_redirect_name(request.user),
                request.user,
            ),
        },
    )


@never_cache
def login_view(request):
    redirect_to = request.POST.get(REDIRECT_FIELD_NAME) or request.GET.get(REDIRECT_FIELD_NAME) or ""
    if request.user.is_authenticated and request.method == "POST" and request.POST.get("action") == "switch_account":
        logout(request)
        query = urlencode({REDIRECT_FIELD_NAME: redirect_to}) if redirect_to else ""
        return redirect(f"{reverse('accounts:login')}?{query}" if query else reverse("accounts:login"))

    if request.method == "GET" and request.user.is_authenticated and request.GET.get("fresh") == "1":
        hidden_fields = {REDIRECT_FIELD_NAME: redirect_to} if redirect_to else {}
        return _switch_account_confirmation(request, target="login", hidden_fields=hidden_fields)

    if request.user.is_authenticated:
        return redirect(_safe_redirect_url(request, redirect_to, _default_redirect_name(request.user), request.user))

    if request.method == "POST":
        identifier = request.POST.get("username", "")
        login_rate_key = _security_rate_key(request, "login", identifier)
        form = EmailAuthenticationForm(request, data=request.POST)
        remaining = _security_lock_remaining(login_rate_key)
        if remaining:
            form.add_error(None, f"Слишком много попыток входа. Попробуйте через {remaining} сек.")
            return render(
                request,
                "accounts/login.html",
                {
                    "form": form,
                    "redirect_field_name": REDIRECT_FIELD_NAME,
                    "redirect_field_value": redirect_to,
                },
                status=429,
            )
        if form.is_valid():
            user = form.get_user()
            _clear_security_rate(login_rate_key)
            if _requires_totp(user):
                request.session["pending_totp_user_id"] = user.pk
                request.session["pending_totp_next"] = redirect_to
                return redirect("accounts:totp")

            login(request, user)
            return redirect(_safe_redirect_url(request, redirect_to, _default_redirect_name(user), user))
        if form.inactive_user:
            _verification_session_set(request, form.inactive_user, redirect_to)
            if not _has_active_email_code(form.inactive_user):
                _send_email_verification_code_safely(request, form.inactive_user)
            return redirect("accounts:verify_email")
        locked_for = _record_security_failure(
            login_rate_key,
            _setting_int("AURUMWEB_LOGIN_MAX_ATTEMPTS", 10),
            _setting_int("AURUMWEB_LOGIN_LOCK_SECONDS", 300),
        )
        if locked_for:
            form.add_error(None, f"Слишком много попыток входа. Вход временно заблокирован на {locked_for // 60} мин.")
    else:
        form = EmailAuthenticationForm(request)

    return render(
        request,
        "accounts/login.html",
        {
            "form": form,
            "redirect_field_name": REDIRECT_FIELD_NAME,
            "redirect_field_value": redirect_to,
        },
    )


def totp_challenge(request):
    user_id = request.session.get("pending_totp_user_id")
    if not user_id:
        return redirect("accounts:login")

    user = User.objects.filter(pk=user_id, is_active=True).first()
    if not user:
        _clear_pending_totp(request)
        return redirect("accounts:login")

    redirect_to = request.session.get("pending_totp_next") or ""
    if not _requires_totp(user):
        login(request, user)
        _clear_pending_totp(request)
        return redirect(_safe_redirect_url(request, redirect_to, _default_redirect_name(user), user))

    profile = _profile(user)
    remaining = _totp_lock_remaining(request, user)
    if request.method == "POST":
        form = TotpAuthenticationForm(request.POST)
        if remaining:
            form.add_error(None, f"Слишком много попыток. Повторите вход через {remaining} сек.")
        elif form.is_valid():
            code = form.cleaned_data["code"]
            if verify_totp(profile.get_totp_secret(), code) or consume_backup_code(user, code):
                _clear_totp_rate(request, user)
                login(request, user)
                _clear_pending_totp(request)
                return redirect(_safe_redirect_url(request, redirect_to, _default_redirect_name(user), user))
            locked_for = _record_totp_failure(request, user)
            if locked_for:
                form.add_error("code", f"Код не подошел. Вход временно заблокирован на {locked_for // 60} минут.")
            else:
                form.add_error("code", "Код не подошел. Проверьте время на телефоне или используйте резервный код.")
    else:
        form = TotpAuthenticationForm()
        if remaining:
            form.add_error(None, f"Слишком много попыток. Повторите вход через {remaining} сек.")

    return render(request, "accounts/totp.html", {"form": form})


def admin_login_redirect(request):
    redirect_to = request.GET.get(REDIRECT_FIELD_NAME) or "/admin/"
    login_url = reverse("accounts:login")
    return redirect(f"{login_url}?{urlencode({REDIRECT_FIELD_NAME: redirect_to})}")


@never_cache
def register(request):
    redirect_to = request.POST.get(REDIRECT_FIELD_NAME) or request.GET.get(REDIRECT_FIELD_NAME) or ""
    telegram_token = (request.POST.get("telegram_registration_token") or request.GET.get("telegram") or "").strip()
    telegram_payload = load_telegram_registration_token(telegram_token)
    if request.user.is_authenticated and telegram_token:
        if request.method == "POST" and request.POST.get("action") == "switch_account" and telegram_payload:
            logout(request)
            query = urlencode({"telegram": telegram_token, REDIRECT_FIELD_NAME: redirect_to})
            return redirect(f"{reverse('accounts:register')}?{query}")
        if request.method == "GET" and telegram_payload:
            hidden_fields = {"telegram_registration_token": telegram_token}
            if redirect_to:
                hidden_fields[REDIRECT_FIELD_NAME] = redirect_to
            return _switch_account_confirmation(request, target="register", hidden_fields=hidden_fields)
    if request.user.is_authenticated:
        return redirect(_safe_redirect_url(request, redirect_to, _default_redirect_name(request.user), request.user))

    if request.method == "POST":
        if rate_limited(
            request,
            "account-register",
            limit=_setting_int("AURUMWEB_REGISTER_RATE_LIMIT", 5),
            window=_setting_int("AURUMWEB_REGISTER_RATE_WINDOW", 300),
        ):
            form = RegistrationForm(request.POST)
            form.add_error(None, TOO_MANY_AUTH_REQUESTS)
            return render(
                request,
                "accounts/register.html",
                {
                    "form": form,
                    "redirect_field_name": REDIRECT_FIELD_NAME,
                    "redirect_field_value": redirect_to,
                    "telegram_registration_token": telegram_token if telegram_payload else "",
                    "telegram_registration_payload": telegram_payload,
                },
                status=429,
            )
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.existing_user or form.save()
            if form.existing_user:
                _verification_session_set(request, user, redirect_to)
                _send_email_verification_code_safely(request, user)
                return redirect("accounts:verify_email")
            if telegram_token:
                if telegram_payload:
                    linked, reason = link_profile_to_telegram(_profile(user), telegram_payload)
                    if linked:
                        messages.success(request, "Telegram подключен к личному кабинету.")
                    elif reason == "chat_already_linked":
                        messages.warning(
                            request,
                            "Этот Telegram уже привязан к другому аккаунту. Если это ошибка, напишите менеджеру.",
                        )
                else:
                    messages.warning(request, "Ссылка Telegram устарела. Подключить бот можно позже в кабинете.")
            _verification_session_set(request, user, redirect_to)
            _send_email_verification_code_safely(request, user)
            return redirect("accounts:verify_email")
    else:
        initial = {"email": request.GET.get("email", "")}
        username = suggested_username(telegram_payload) if telegram_payload else ""
        if username and not User.objects.filter(username__iexact=username).exists():
            initial["username"] = username
        form = RegistrationForm(initial=initial)
    return render(
        request,
        "accounts/register.html",
        {
            "form": form,
            "redirect_field_name": REDIRECT_FIELD_NAME,
            "redirect_field_value": redirect_to,
            "telegram_registration_token": telegram_token if telegram_payload else "",
            "telegram_registration_payload": telegram_payload,
        },
    )


def verify_email(request):
    user = _pending_verification_user(request)
    if not user:
        messages.warning(request, "Сначала создайте аккаунт или войдите с email и паролем.")
        return redirect("accounts:register")

    redirect_to = request.session.get("pending_email_next") or ""
    if request.method == "POST":
        form = EmailVerificationForm(request.POST)
        if form.is_valid():
            verification_code = (
                EmailVerificationCode.objects.filter(
                    user=user,
                    purpose=EmailVerificationCode.Purpose.EMAIL_VERIFY,
                    used_at__isnull=True,
                )
                .order_by("-created_at")
                .first()
            )
            if not verification_code:
                form.add_error("code", "Активного кода нет. Запросите новый код.")
            else:
                success, reason = verification_code.verify(form.cleaned_data["code"])
                if success:
                    if user.is_active:
                        _verification_session_clear(request)
                        messages.success(request, "Email подтвержден. Войдите с паролем.")
                        return redirect("accounts:login")
                    user.is_active = True
                    user.save(update_fields=("is_active",))
                    login(request, user)
                    _verification_session_clear(request)
                    request.session.modified = True
                    target_url = _safe_redirect_url(request, redirect_to, "client_portal:dashboard", user)
                    if urlparse(target_url).path == reverse("accounts:verify_email"):
                        target_url = reverse("client_portal:dashboard")
                    logger.info("Email verified for user_id=%s redirect=%s", user.pk, target_url)
                    messages.success(request, "Email подтвержден. Кабинет готов к работе.")
                    return redirect(target_url)
                error_messages = {
                    "expired": "Код истек. Запросите новый код.",
                    "locked": "Слишком много неверных попыток. Запросите новый код.",
                    "used": "Этот код уже использован. Запросите новый код.",
                    "invalid": "Код не подошел. Если запрашивали новый код, используйте последнее письмо.",
                }
                message = error_messages.get(reason, "Код не подошел.")
                form.add_error("code", message)
                messages.error(request, message)
                logger.warning("Email verification code rejected for user_id=%s reason=%s", user.pk, reason)
    else:
        form = EmailVerificationForm()

    return render(
        request,
        "accounts/verify_email.html",
        {
            "form": form,
            "email": user.email,
        },
    )


@require_POST
def resend_email_verification(request):
    user = _pending_verification_user(request)
    if not user:
        messages.warning(request, "Сначала создайте аккаунт или войдите с email и паролем.")
        return redirect("accounts:register")
    if user.is_active:
        _verification_session_clear(request)
        return redirect("accounts:login")

    cache_key = f"email-code-resend:{user.pk}:{_client_ip(request)}"
    if cache.get(cache_key):
        messages.warning(request, "Новый код можно запросить раз в минуту.")
        return redirect("accounts:verify_email")

    if _send_email_verification_code_safely(request, user):
        cache.set(cache_key, True, timeout=EMAIL_CODE_RESEND_SECONDS)
        messages.success(request, "Новый код отправлен на email.")
    return redirect("accounts:verify_email")


class RateLimitedPasswordResetView(auth_views.PasswordResetView):
    def post(self, request, *args, **kwargs):
        if rate_limited(
            request,
            "password-reset",
            limit=_setting_int("AURUMWEB_PASSWORD_RESET_RATE_LIMIT", 5),
            window=_setting_int("AURUMWEB_PASSWORD_RESET_RATE_WINDOW", 300),
        ):
            form = self.get_form()
            form.add_error(None, TOO_MANY_AUTH_REQUESTS)
            return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)
