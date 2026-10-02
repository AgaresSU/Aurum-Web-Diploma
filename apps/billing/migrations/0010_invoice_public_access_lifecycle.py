from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


def backfill_public_access_deadline(apps, schema_editor):
    Invoice = apps.get_model("billing", "Invoice")
    Invoice.objects.filter(public_access_expires_at__isnull=True).update(
        public_access_expires_at=timezone.now() + timedelta(days=30)
    )


def clear_public_access_deadline(apps, schema_editor):
    Invoice = apps.get_model("billing", "Invoice")
    Invoice.objects.update(public_access_expires_at=None)


class Migration(migrations.Migration):
    dependencies = [("billing", "0009_invoice_project_managedsite_project_order_project")]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="public_access_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="invoice",
            name="public_access_revoked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            backfill_public_access_deadline,
            clear_public_access_deadline,
        ),
    ]
