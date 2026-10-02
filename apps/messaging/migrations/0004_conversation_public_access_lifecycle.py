from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


def backfill_public_access_deadline(apps, schema_editor):
    Conversation = apps.get_model("messaging", "Conversation")
    Conversation.objects.filter(public_access_expires_at__isnull=True).update(
        public_access_expires_at=timezone.now() + timedelta(days=30)
    )


def clear_public_access_deadline(apps, schema_editor):
    Conversation = apps.get_model("messaging", "Conversation")
    Conversation.objects.update(public_access_expires_at=None)


class Migration(migrations.Migration):
    dependencies = [("messaging", "0003_conversation_client_last_read_at_and_more")]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="public_access_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="conversation",
            name="public_access_revoked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            backfill_public_access_deadline,
            clear_public_access_deadline,
        ),
    ]
