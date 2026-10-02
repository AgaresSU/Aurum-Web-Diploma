from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_profile_billing_address_profile_client_type_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="totp_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="profile",
            name="totp_secret",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
