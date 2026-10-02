import re

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings

from .models import EmailVerificationCode, Profile
from .telegram_registration import make_telegram_registration_token
from .totp import generate_totp_secret, totp_code


@override_settings(AURUMWEB_REGISTRATION_EMAIL_ALLOWED_SUFFIXES=(".test", ".ru", ".su", ".рф", ".рус", ".moscow"))
class LoginRoutingTests(TestCase):
    def setUp(self):
        cache.clear()

    def _user(self, username, email, password="StrongPass123!", role=Profile.Role.CLIENT, staff=False, superuser=False):
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            is_staff=staff,
            is_superuser=superuser,
        )
        Profile.objects.create(user=user, role=role)
        return user

    def _enable_totp(self, user):
        secret = generate_totp_secret()
        user.profile.set_totp_secret(secret)
        user.profile.totp_enabled = True
        user.profile.save(update_fields=("totp_secret", "totp_enabled"))
        return secret

    def test_client_login_goes_to_client_portal(self):
        self._user("client", "client@example.test")
        response = self.client.post(
            "/accounts/login/",
            {"username": "client@example.test", "password": "StrongPass123!"},
        )
        self.assertRedirects(response, "/client/", fetch_redirect_response=False)

    @override_settings(AURUMWEB_LOGIN_MAX_ATTEMPTS=2, AURUMWEB_LOGIN_LOCK_SECONDS=300)
    def test_login_rate_limit_blocks_repeated_bad_passwords(self):
        self._user("client", "client@example.test")
        payload = {"username": "client@example.test", "password": "WrongPass123!"}

        self.assertEqual(self.client.post("/accounts/login/", payload).status_code, 200)
        self.assertEqual(self.client.post("/accounts/login/", payload).status_code, 200)
        response = self.client.post("/accounts/login/", payload)

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Слишком много попыток входа", status_code=429)

    @override_settings(
        AURUMWEB_TRUST_X_FORWARDED_FOR=True,
        AURUMWEB_LOGIN_MAX_ATTEMPTS=2,
        AURUMWEB_LOGIN_LOCK_SECONDS=300,
    )
    def test_forwarded_header_prefix_cannot_rotate_login_rate_key(self):
        self._user("client", "client@example.test")
        payload = {"username": "client@example.test", "password": "WrongPass123!"}

        self.client.post("/accounts/login/", payload, HTTP_X_FORWARDED_FOR="spoof-a, 198.51.100.10")
        self.client.post("/accounts/login/", payload, HTTP_X_FORWARDED_FOR="spoof-b, 198.51.100.10")
        response = self.client.post(
            "/accounts/login/",
            payload,
            HTTP_X_FORWARDED_FOR="spoof-c, 198.51.100.10",
        )

        self.assertEqual(response.status_code, 429)

    @override_settings(AURUMWEB_TRUST_X_FORWARDED_FOR=True)
    def test_totp_lock_is_account_wide_when_forwarded_address_changes(self):
        user = self._user("admin", "admin@example.test", role=Profile.Role.ADMIN, staff=True)
        secret = self._enable_totp(user)
        self.client.post(
            "/accounts/login/",
            {"username": "admin@example.test", "password": "StrongPass123!"},
        )
        valid_code = totp_code(secret)
        wrong_code = "111111" if valid_code == "000000" else "000000"
        for index in range(5):
            self.client.post(
                "/accounts/login/2fa/",
                {"code": wrong_code},
                HTTP_X_FORWARDED_FOR=f"203.0.113.{index}",
            )

        response = self.client.post(
            "/accounts/login/2fa/",
            {"code": valid_code},
            HTTP_X_FORWARDED_FOR="203.0.113.99",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Слишком много попыток")
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(AURUMWEB_LOGIN_MAX_ATTEMPTS=2, AURUMWEB_LOGIN_LOCK_SECONDS=300)
    def test_successful_login_clears_previous_bad_password_attempt(self):
        self._user("client", "client@example.test")

        self.client.post(
            "/accounts/login/",
            {"username": "client@example.test", "password": "WrongPass123!"},
        )
        response = self.client.post(
            "/accounts/login/",
            {"username": "client@example.test", "password": "StrongPass123!"},
        )

        self.assertRedirects(response, "/client/", fetch_redirect_response=False)

    def test_client_next_office_is_forced_back_to_client_portal(self):
        self._user("client", "client@example.test")
        response = self.client.post(
            "/accounts/login/?next=/office/",
            {"username": "client@example.test", "password": "StrongPass123!"},
        )
        self.assertRedirects(response, "/client/", fetch_redirect_response=False)

    def test_admin_login_goes_to_office(self):
        self._user("admin", "admin@example.test", role=Profile.Role.ADMIN, staff=True)
        response = self.client.post(
            "/accounts/login/",
            {"username": "admin@example.test", "password": "StrongPass123!"},
        )
        self.assertRedirects(response, "/office/security/", fetch_redirect_response=False)

    def test_admin_next_client_is_forced_back_to_office(self):
        self._user("admin", "admin@example.test", role=Profile.Role.ADMIN, staff=True)
        response = self.client.post(
            "/accounts/login/?next=/client/",
            {"username": "admin@example.test", "password": "StrongPass123!"},
        )
        self.assertRedirects(response, "/office/security/", fetch_redirect_response=False)

    def test_admin_with_totp_must_pass_second_factor(self):
        user = self._user("admin", "admin@example.test", role=Profile.Role.ADMIN, staff=True)
        secret = self._enable_totp(user)

        response = self.client.post(
            "/accounts/login/",
            {"username": "admin@example.test", "password": "StrongPass123!"},
        )
        self.assertRedirects(response, "/accounts/login/2fa/", fetch_redirect_response=False)

        response = self.client.post("/accounts/login/2fa/", {"code": totp_code(secret)})
        self.assertRedirects(response, "/office/", fetch_redirect_response=False)

    def test_django_admin_requires_superuser_and_totp(self):
        manager = self._user("manager", "manager@example.test", role=Profile.Role.ADMIN, staff=True, superuser=False)
        self._enable_totp(manager)
        self.client.force_login(manager)

        response = self.client.get("/admin/")
        self.assertRedirects(response, "/office/", fetch_redirect_response=False)

        self.client.logout()
        owner = self._user("owner", "owner@example.test", role=Profile.Role.ADMIN, staff=True, superuser=True)
        self._enable_totp(owner)
        self.client.force_login(owner)

        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AurumWeb Admin")

    def test_admin_without_totp_cannot_use_office_or_admin_until_setup(self):
        user = self._user("admin", "admin@example.test", role=Profile.Role.ADMIN, staff=True)
        self.client.force_login(user)

        for path in ("/office/", "/office/conversations/", "/admin/"):
            response = self.client.get(path)
            self.assertRedirects(response, "/office/security/", fetch_redirect_response=False)

        response = self.client.get("/office/security/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Нужно включить второй код входа")

    def test_admin_totp_setup_requires_valid_first_code(self):
        user = self._user("admin", "admin@example.test", role=Profile.Role.ADMIN, staff=True)
        self.client.force_login(user)

        response = self.client.get("/office/security/")
        self.assertEqual(response.status_code, 200)
        secret = self.client.session["pending_totp_secret"]

        response = self.client.post(
            "/office/security/",
            {"action": "confirm_totp_setup", "password": "StrongPass123!", "code": "000000"},
        )
        user.profile.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(user.profile.totp_enabled)

        response = self.client.post(
            "/office/security/",
            {"action": "confirm_totp_setup", "password": "StrongPass123!", "code": totp_code(secret)},
        )
        self.assertRedirects(response, "/office/security/", fetch_redirect_response=False)
        user.profile.refresh_from_db()
        self.assertTrue(user.profile.totp_enabled)
        self.assertTrue(user.profile.get_totp_secret())

    def test_register_prefills_email_and_keeps_safe_next(self):
        response = self.client.get("/accounts/register/?email=client@example.test&next=/client/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="client@example.test"')
        self.assertContains(response, 'name="next" value="/client/"')

    def test_register_from_telegram_token_links_profile(self):
        token = make_telegram_registration_token(chat_id="999", username="client_tg")

        response = self.client.get(f"/accounts/register/?telegram={token}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="telegram_registration_token"')
        self.assertContains(response, 'value="client_tg"')

        response = self.client.post(
            "/accounts/register/",
            {
                "username": "new-client",
                "email": "new-client@example.test",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
                "telegram_registration_token": token,
            },
        )

        self.assertRedirects(response, "/accounts/verify-email/", fetch_redirect_response=False)
        user = User.objects.get(username="new-client")
        profile = Profile.objects.get(user=user)
        self.assertEqual(profile.telegram_chat_id, "999")
        self.assertEqual(profile.telegram_username, "@client_tg")
        self.assertTrue(profile.telegram_notifications_enabled)
        self.assertEqual(len(mail.outbox), 1)

    def test_telegram_registration_link_logs_out_existing_session(self):
        existing_user = self._user("existing", "existing@example.test")
        self.client.force_login(existing_user)
        token = make_telegram_registration_token(chat_id="999", username="client_tg")

        response = self.client.get(f"/accounts/register/?telegram={token}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertContains(response, "Сменить аккаунт?")

        response = self.client.post(
            f"/accounts/register/?telegram={token}",
            {"action": "switch_account", "telegram_registration_token": token},
        )

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_fresh_login_link_logs_out_existing_session(self):
        existing_user = self._user("existing", "existing@example.test")
        self.client.force_login(existing_user)

        response = self.client.get("/accounts/login/?fresh=1")

        self.assertEqual(response.status_code, 200)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertContains(response, "Сменить аккаунт?")

        response = self.client.post("/accounts/login/?fresh=1", {"action": "switch_account"})

        self.assertRedirects(response, "/accounts/login/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_register_handles_existing_email_without_public_disclosure(self):
        self._user("client", "client@example.test")

        response = self.client.post(
            "/accounts/register/",
            {
                "username": "new-client",
                "email": "CLIENT@example.test",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertRedirects(response, "/accounts/verify-email/", fetch_redirect_response=False)
        self.assertEqual(User.objects.filter(email__iexact="client@example.test").count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_register_rejects_foreign_email_domain(self):
        response = self.client.post(
            "/accounts/register/",
            {
                "username": "gmail-client",
                "email": "client@gmail.com",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "только российские email-домены")
        self.assertFalse(User.objects.filter(username="gmail-client").exists())
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(AURUMWEB_REGISTER_RATE_LIMIT=1, AURUMWEB_REGISTER_RATE_WINDOW=300)
    def test_register_rate_limit_blocks_burst_signups(self):
        first_response = self.client.post(
            "/accounts/register/",
            {
                "username": "new-client",
                "email": "new-client@example.test",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )
        second_response = self.client.post(
            "/accounts/register/",
            {
                "username": "second-client",
                "email": "second-client@example.test",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertRedirects(first_response, "/accounts/verify-email/", fetch_redirect_response=False)
        self.assertEqual(second_response.status_code, 429)
        self.assertContains(second_response, "Слишком много попыток", status_code=429)
        self.assertFalse(User.objects.filter(username="second-client").exists())

    def test_register_redirects_client_to_requested_client_page(self):
        response = self.client.post(
            "/accounts/register/?next=/client/conversations/",
            {
                "username": "new-client",
                "email": "new-client@example.test",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertRedirects(response, "/accounts/verify-email/", fetch_redirect_response=False)
        user = User.objects.get(username="new-client")
        self.assertFalse(user.is_active)
        self.assertEqual(len(mail.outbox), 1)

        code = re.search(r"\b(\d{6})\b", mail.outbox[0].body).group(1)
        response = self.client.post("/accounts/verify-email/", {"code": code})

        self.assertRedirects(response, "/client/conversations/", fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_register_blocks_admin_next_for_new_client(self):
        response = self.client.post(
            "/accounts/register/?next=/office/",
            {
                "username": "new-client",
                "email": "new-client@example.test",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertRedirects(response, "/accounts/verify-email/", fetch_redirect_response=False)
        code = re.search(r"\b(\d{6})\b", mail.outbox[0].body).group(1)
        response = self.client.post("/accounts/verify-email/", {"code": code})

        self.assertRedirects(response, "/client/", fetch_redirect_response=False)

    def test_email_confirmation_never_redirects_back_to_confirmation_page(self):
        response = self.client.post(
            "/accounts/register/?next=/accounts/verify-email/",
            {
                "username": "self-redirect-client",
                "email": "self-redirect-client@example.test",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )
        self.assertRedirects(response, "/accounts/verify-email/", fetch_redirect_response=False)

        code = re.search(r"\b(\d{6})\b", mail.outbox[0].body).group(1)
        response = self.client.post("/accounts/verify-email/", {"code": code})

        self.assertRedirects(response, "/client/", fetch_redirect_response=False)

    def test_inactive_user_can_return_to_email_confirmation(self):
        user = User.objects.create_user(
            username="pending",
            email="pending@example.test",
            password="StrongPass123!",
            is_active=False,
        )
        Profile.objects.create(user=user)
        EmailVerificationCode.issue(user, EmailVerificationCode.Purpose.EMAIL_VERIFY, email=user.email)

        response = self.client.post(
            "/accounts/login/",
            {"username": "pending@example.test", "password": "StrongPass123!"},
        )

        self.assertRedirects(response, "/accounts/verify-email/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["pending_email_user_id"], user.pk)

    def test_email_confirmation_rejects_wrong_code(self):
        response = self.client.post(
            "/accounts/register/",
            {
                "username": "wrong-code",
                "email": "wrong-code@example.test",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )
        self.assertRedirects(response, "/accounts/verify-email/", fetch_redirect_response=False)

        response = self.client.post("/accounts/verify-email/", {"code": "000000"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Код не подошел")
        self.assertFalse(User.objects.get(username="wrong-code").is_active)

    def test_verify_email_resend_button_skips_required_code_validation(self):
        response = self.client.post(
            "/accounts/register/",
            {
                "username": "resend-button",
                "email": "resend-button@example.test",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )
        self.assertRedirects(response, "/accounts/verify-email/", fetch_redirect_response=False)

        response = self.client.get("/accounts/verify-email/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "formnovalidate")
        self.assertContains(response, 'formaction="/accounts/verify-email/resend/"')

    def test_resend_email_confirmation_replaces_active_code(self):
        response = self.client.post(
            "/accounts/register/",
            {
                "username": "resend-code",
                "email": "resend-code@example.test",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )
        self.assertRedirects(response, "/accounts/verify-email/", fetch_redirect_response=False)

        response = self.client.post("/accounts/verify-email/resend/")

        self.assertRedirects(response, "/accounts/verify-email/", fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(
            EmailVerificationCode.objects.filter(user__username="resend-code", used_at__isnull=True).count(),
            1,
        )

    def test_password_reset_sends_email_for_active_user(self):
        self._user("client-reset", "client-reset@example.test")

        response = self.client.post("/accounts/password-reset/", {"email": "client-reset@example.test"})

        self.assertRedirects(response, "/accounts/password-reset/done/", fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/accounts/reset/", mail.outbox[0].body)

    @override_settings(AURUMWEB_PASSWORD_RESET_RATE_LIMIT=1, AURUMWEB_PASSWORD_RESET_RATE_WINDOW=300)
    def test_password_reset_rate_limit_blocks_burst_requests(self):
        self._user("client-reset", "client-reset@example.test")

        first_response = self.client.post("/accounts/password-reset/", {"email": "client-reset@example.test"})
        second_response = self.client.post("/accounts/password-reset/", {"email": "client-reset@example.test"})

        self.assertRedirects(first_response, "/accounts/password-reset/done/", fetch_redirect_response=False)
        self.assertEqual(second_response.status_code, 429)
        self.assertContains(second_response, "Слишком много попыток", status_code=429)
        self.assertEqual(len(mail.outbox), 1)

    def test_password_reset_confirm_changes_password(self):
        user = self._user("client-reset", "client-reset@example.test")

        response = self.client.post("/accounts/password-reset/", {"email": "client-reset@example.test"})
        self.assertRedirects(response, "/accounts/password-reset/done/", fetch_redirect_response=False)
        reset_path = re.search(r"https?://[^/]+(?P<path>/accounts/reset/\S+/)", mail.outbox[0].body).group("path")

        response = self.client.get(reset_path)
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            response["Location"],
            {
                "new_password1": "NewStrongPass123!",
                "new_password2": "NewStrongPass123!",
            },
        )

        self.assertRedirects(response, "/accounts/reset/done/", fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertTrue(user.check_password("NewStrongPass123!"))

    def test_password_reset_does_not_send_email_for_inactive_user(self):
        user = User.objects.create_user(
            username="pending-reset",
            email="pending-reset@example.test",
            password="StrongPass123!",
            is_active=False,
        )
        Profile.objects.create(user=user)

        response = self.client.post("/accounts/password-reset/", {"email": "pending-reset@example.test"})

        self.assertRedirects(response, "/accounts/password-reset/done/", fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 0)
