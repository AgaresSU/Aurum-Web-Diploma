from io import StringIO

from cryptography.fernet import Fernet
from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.accounts.crypto import PREFIX, decrypt_value, encrypt_value, rotate_encrypted_value
from apps.integrations.models import RobokassaSettings


class EncryptionKeyringTests(TestCase):
    def setUp(self):
        self.old_key = Fernet.generate_key().decode("ascii")
        self.new_key = Fernet.generate_key().decode("ascii")

    def _old_ciphertext(self, value):
        token = Fernet(self.old_key.encode("ascii")).encrypt(value.encode("utf-8")).decode("ascii")
        return PREFIX + token

    def test_old_key_can_decrypt_and_primary_key_encrypts(self):
        encrypted = self._old_ciphertext("payment-secret")
        with override_settings(
            AURUMWEB_TOTP_ENCRYPTION_KEY=self.new_key,
            AURUMWEB_TOTP_ENCRYPTION_OLD_KEYS=(self.old_key,),
            AURUMWEB_ALLOW_LEGACY_SECRET_KEY_DECRYPTION=False,
        ):
            self.assertEqual(decrypt_value(encrypted), "payment-secret")
            rotated = rotate_encrypted_value(encrypted)
            self.assertEqual(decrypt_value(rotated), "payment-secret")
            self.assertNotEqual(rotated, encrypted)
            self.assertEqual(decrypt_value(encrypt_value("new-secret")), "new-secret")

    def test_rotation_command_reencrypts_integration_secrets(self):
        config = RobokassaSettings.objects.create(
            password1=self._old_ciphertext("password-one"),
            password2=self._old_ciphertext("password-two"),
        )
        with override_settings(
            AURUMWEB_TOTP_ENCRYPTION_KEY=self.new_key,
            AURUMWEB_TOTP_ENCRYPTION_OLD_KEYS=(self.old_key,),
            AURUMWEB_ALLOW_LEGACY_SECRET_KEY_DECRYPTION=False,
        ):
            call_command("rotate_encryption_key", stdout=StringIO())
            config.refresh_from_db()
            self.assertEqual(config.get_password1(), "password-one")
            self.assertEqual(config.get_password2(), "password-two")
            with override_settings(AURUMWEB_TOTP_ENCRYPTION_OLD_KEYS=()):
                self.assertEqual(config.get_password1(), "password-one")
