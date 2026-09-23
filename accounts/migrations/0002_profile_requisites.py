from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [
        migrations.AddField(model_name="profile", name="billing_address", field=models.CharField(blank=True, max_length=260)),
        migrations.AddField(model_name="profile", name="client_type", field=models.CharField(choices=[("individual", "Физлицо"), ("sole_proprietor", "ИП"), ("company", "Юрлицо")], default="individual", max_length=30)),
        migrations.AddField(model_name="profile", name="contract_contact", field=models.CharField(blank=True, max_length=160)),
        migrations.AddField(model_name="profile", name="inn", field=models.CharField(blank=True, max_length=12)),
        migrations.AddField(model_name="profile", name="kpp", field=models.CharField(blank=True, max_length=9)),
        migrations.AddField(model_name="profile", name="legal_name", field=models.CharField(blank=True, max_length=220)),
        migrations.AddField(model_name="profile", name="ogrn", field=models.CharField(blank=True, max_length=15)),
        migrations.AddField(model_name="profile", name="personal_data_consent", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="profile", name="requisites_comment", field=models.TextField(blank=True)),
        migrations.AddField(model_name="profile", name="telegram_chat_id", field=models.CharField(blank=True, max_length=80)),
        migrations.AddField(model_name="profile", name="telegram_link_code", field=models.CharField(blank=True, max_length=24)),
        migrations.AddField(model_name="profile", name="telegram_notifications_enabled", field=models.BooleanField(default=False)),
    ]
