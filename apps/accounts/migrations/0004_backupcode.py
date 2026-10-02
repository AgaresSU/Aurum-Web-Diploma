from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("accounts", "0003_profile_totp"),
    ]

    operations = [
        migrations.CreateModel(
            name="BackupCode",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code_hash", models.CharField(max_length=128)),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="backup_codes", to=settings.AUTH_USER_MODEL),
                ),
            ],
            options={
                "verbose_name": "Резервный код",
                "verbose_name_plural": "Резервные коды",
            },
        ),
    ]
