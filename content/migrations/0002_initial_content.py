from django.db import migrations


def add_initial_content(apps, schema_editor):
    Service = apps.get_model('content', 'Service')
    Work = apps.get_model('content', 'Work')

    services = [
        {
            'slug': 'website',
            'title': 'Сайт под ключ',
            'short_description': 'Лендинги, корпоративные сайты и небольшие каталоги.',
            'full_description': 'Сайт для компании, специалиста или небольшого проекта.',
            'sort_order': 10,
        },
        {
            'slug': 'support',
            'title': 'Поддержка сайта',
            'short_description': 'Обновление страниц, исправление ошибок и небольшие доработки.',
            'full_description': 'Помощь с уже работающим сайтом после запуска.',
            'sort_order': 20,
        },
        {
            'slug': 'python',
            'title': 'Python-автоматизация',
            'short_description': 'Программы для заявок, документов и повторяющихся задач.',
            'full_description': 'Небольшие программы, которые сокращают ручную работу.',
            'sort_order': 30,
        },
        {
            'slug': 'telegram',
            'title': 'Telegram-бот',
            'short_description': 'Боты для консультаций, заявок и уведомлений.',
            'full_description': 'Бот задаёт вопросы, принимает контакты и отправляет уведомления.',
            'sort_order': 40,
        },
    ]
    for item in services:
        slug = item.pop('slug')
        Service.objects.update_or_create(slug=slug, defaults=item)

    works = [
        {
            'slug': 'it-company',
            'title': 'Корпоративный сайт IT-компании',
            'category': 'Корпоративный сайт',
            'short_description': 'Сайт компании с рассказом об услугах, проектах и технологиях.',
            'description': 'Корпоративный сайт с главным экраном, услугами и формой обращения.',
            'features': 'Главный экран\nРаздел услуг\nФорма обращения',
            'preview_static': 'images/examples/it-company-corporate.png',
            'demo_static': 'demos/it-company-corporate/index.html',
        },
        {
            'slug': 'coffee-house',
            'title': 'Лендинг для кофейни',
            'category': 'Лендинг',
            'short_description': 'Одностраничный сайт кофейни с меню, преимуществами и контактами.',
            'description': 'Небольшой сайт для кофейни с меню, фотографиями и контактами.',
            'features': 'Главная страница\nМеню\nКонтакты',
            'preview_static': 'images/examples/coffee-house-landing.png',
            'demo_static': 'demos/coffee-house-landing/index.html',
        },
    ]
    for item in works:
        slug = item.pop('slug')
        Work.objects.update_or_create(slug=slug, defaults=item)


def remove_initial_content(apps, schema_editor):
    Service = apps.get_model('content', 'Service')
    Work = apps.get_model('content', 'Work')
    Service.objects.filter(slug__in=['website', 'support', 'python', 'telegram']).delete()
    Work.objects.filter(slug__in=['it-company', 'coffee-house']).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('content', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(add_initial_content, remove_initial_content),
    ]
