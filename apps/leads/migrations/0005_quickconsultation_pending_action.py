from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("leads", "0004_quickconsultation_legal_flow"),
    ]

    operations = [
        migrations.AddField(
            model_name="quickconsultation",
            name="pending_action",
            field=models.CharField(
                blank=True,
                choices=[
                    ("menu", "Меню"),
                    ("consultation", "Консультация"),
                    ("payment", "Оплата"),
                ],
                default="",
                max_length=30,
            ),
        ),
    ]
