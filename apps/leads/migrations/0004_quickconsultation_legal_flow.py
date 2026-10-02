from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("leads", "0003_alter_quickconsultation_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="quickconsultation",
            name="client_email",
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name="quickconsultation",
            name="offer_accepted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="quickconsultation",
            name="privacy_accepted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="quickconsultation",
            name="status",
            field=models.CharField(
                choices=[
                    ("waiting_offer", "Ждем принятия оферты"),
                    ("waiting_privacy", "Ждем согласие с политикой"),
                    ("waiting_email", "Ждем email"),
                    ("waiting_email_confirm", "Подтверждение email"),
                    ("waiting_question", "Ждем вопрос"),
                    ("waiting_payment_amount", "Ждем сумму оплаты"),
                    ("new", "Новая"),
                    ("in_discussion", "В консультации"),
                    ("invoiced", "Счет отправлен"),
                    ("paid", "Оплачена"),
                    ("closed", "Закрыта"),
                    ("cancelled", "Отменена"),
                ],
                default="waiting_question",
                max_length=32,
            ),
        ),
    ]
