import os
import subprocess
import sys

from cryptography.fernet import Fernet
from django.test import SimpleTestCase


class ProductionConfigurationTests(SimpleTestCase):
    def _settings_import(self, **overrides):
        env = os.environ.copy()
        env.update(
            {
                "DJANGO_ENV_FILE": "",
                "DJANGO_DEBUG": "0",
                "DJANGO_SECRET_KEY": "production-test-secret-key-not-for-use",
                "DJANGO_ALLOWED_HOSTS": "example.test",
                "DJANGO_CSRF_TRUSTED_ORIGINS": "https://example.test",
                "DATABASE_URL": "postgres://user:password@127.0.0.1:5432/aurumweb",
                "AURUMWEB_TOTP_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
            }
        )
        env.update(overrides)
        return subprocess.run(
            [sys.executable, "-c", "import devforma.settings; print('settings-ok')"],
            cwd=os.getcwd(),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_production_requires_independent_encryption_key(self):
        result = self._settings_import(AURUMWEB_TOTP_ENCRYPTION_KEY="")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AURUMWEB_TOTP_ENCRYPTION_KEY", result.stderr)

    def test_production_rejects_sqlite_without_emergency_flag(self):
        result = self._settings_import(DATABASE_URL="sqlite:///:memory:")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SQLite is disabled for production", result.stderr)

    def test_invalid_encryption_key_is_rejected_at_startup(self):
        result = self._settings_import(AURUMWEB_TOTP_ENCRYPTION_KEY="not-a-fernet-key")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("valid Fernet keys", result.stderr)

    def test_emergency_sqlite_requires_explicit_flag(self):
        result = self._settings_import(
            DATABASE_URL="sqlite:///:memory:",
            AURUMWEB_ALLOW_PRODUCTION_SQLITE="1",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("settings-ok", result.stdout)
