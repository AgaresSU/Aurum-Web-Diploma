from django.db import migrations, models


def encrypt_existing_totp_secrets(apps, schema_editor):
    from apps.accounts.crypto import encrypt_value

    Profile = apps.get_model("accounts", "Profile")
    for profile in Profile.objects.exclude(totp_secret=""):
        if not profile.totp_secret.startswith("fernet:"):
            profile.totp_secret = encrypt_value(profile.totp_secret)
            profile.save(update_fields=("totp_secret",))


def decrypt_existing_totp_secrets(apps, schema_editor):
    from apps.accounts.crypto import decrypt_value

    Profile = apps.get_model("accounts", "Profile")
    for profile in Profile.objects.exclude(totp_secret=""):
        if profile.totp_secret.startswith("fernet:"):
            profile.totp_secret = decrypt_value(profile.totp_secret)
            profile.save(update_fields=("totp_secret",))


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_backupcode"),
    ]

    operations = [
        migrations.AlterField(
            model_name="profile",
            name="totp_secret",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.RunPython(encrypt_existing_totp_secrets, decrypt_existing_totp_secrets),
    ]
