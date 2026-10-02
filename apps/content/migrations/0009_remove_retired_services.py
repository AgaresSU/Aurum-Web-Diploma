from django.db import migrations


ACTIVE_SERVICE_SLUGS = {
    "turnkey-websites",
    "service-landing-page",
    "catalog-showcase",
    "client-portal",
    "website-care-support",
    "telegram-bots",
    "python-tools-automation",
    "payments-and-integrations",
    "analytics-search-setup",
    "technical-consulting",
}


def remove_retired_services(apps, schema_editor):
    service = apps.get_model("content", "Service")
    service.objects.exclude(slug__in=ACTIVE_SERVICE_SLUGS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0008_sync_public_cases_and_template_catalog"),
    ]

    operations = [
        migrations.RunPython(remove_retired_services, migrations.RunPython.noop),
    ]
