from django.db import migrations


SERVICE_TEXTS = {
    "turnkey-websites": "Сайт ясно показывает услуги, принимает заявки и помогает быстро перейти от интереса клиента к обсуждению проекта.",
    "website-care-support": "Поддержка держит сайт в рабочем состоянии: сроки оплат, безопасность, правки и новые задачи остаются под контролем.",
    "python-tools-automation": "Небольшие программы закрывают повторяющиеся операции: отчеты, таблицы, файлы, парсинг и обмен данными между сервисами.",
    "telegram-bots": "Бот принимает обращения, отправляет уведомления и помогает не пропускать важные сообщения от клиентов.",
    "technical-consulting": "Консультация дает понятный план: что делать, в каком порядке, сколько это может стоить и где есть риски.",
}


def refresh_public_service_copy(apps, schema_editor):
    Service = apps.get_model("content", "Service")
    for slug, business_value in SERVICE_TEXTS.items():
        Service.objects.filter(slug=slug).update(business_value=business_value)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0005_seed_service_prices_and_consultation"),
    ]

    operations = [
        migrations.RunPython(refresh_public_service_copy, noop_reverse),
    ]
