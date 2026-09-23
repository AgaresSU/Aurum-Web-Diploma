import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="ClientRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("title", models.CharField(max_length=220)),
                ("service_type", models.CharField(max_length=120)),
                ("summary", models.TextField()),
                ("template_slug", models.SlugField(blank=True, max_length=160)),
                ("status", models.CharField(choices=[("new", "Новая задача"), ("in_progress", "В работе"), ("completed", "Завершена")], default="new", max_length=20)),
                ("due_at", models.DateField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("client", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="client_requests", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "Задача клиента", "verbose_name_plural": "Задачи клиентов", "ordering": ("-created_at",)},
        ),
    ]
