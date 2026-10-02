from django.db import migrations


ACTIVE_TEMPLATE_SLUGS = {
    "business-technology-conference",
    "analytics-startup-platform",
    "apartment-renovation-landing",
    "app-healthy-nutrition",
    "auto-service-landing",
    "beauty-salon-landing",
    "branding-agency-showcase",
    "business-app-landing",
    "business-masterclass-event",
    "cargo-freight-landing",
    "cleaning-service-company",
    "coffee-house-landing",
    "creative-web-studio",
    "event-agency-portfolio",
    "fashion-atelier-studio",
    "food-delivery-market",
    "game-app-development",
    "home-design-store",
    "interior-architecture-studio",
    "it-company-corporate",
    "it-outstaffing-team",
    "legal-consulting-services",
    "online-language-school",
    "pipe-manufacturing-corporate",
    "residential-complex-breeze",
    "salon-equipment-catalog",
    "taxi-fleet-landing",
}


PUBLIC_CASES = (
    {
        "slug": "lead-invoice-center",
        "title": "Единый центр заявок и счетов",
        "industry": "B2B-услуги",
        "challenge": "Заявки приходят из разных форм, теряются в переписках и не связаны со счетами.",
        "solution": "Панель управления, статусы заявок, диалоги, черновики счетов и прием оплаты.",
        "result": "Владелец видит весь путь клиента: задача, разбор, предложение, счет и оплата.",
        "stack": "Сайт, личный кабинет, счета, платежи и Telegram-уведомления",
        "sort_order": 10,
        "is_featured": True,
    },
    {
        "slug": "expert-website-seo-structure",
        "title": "Структура сайта услуг",
        "industry": "Консалтинг",
        "challenge": "Сайт выглядит аккуратно, но не помогает посетителю быстро найти нужную услугу.",
        "solution": "Направления услуг, понятные описания, ответы на вопросы, карта страниц и подготовка к публикации.",
        "result": "Сайт можно развивать и продвигать без переделки структуры.",
        "stack": "Шаблоны страниц, карта сайта и подготовка для поиска",
        "sort_order": 20,
        "is_featured": True,
    },
)


def sync_public_content(apps, schema_editor):
    case_study = apps.get_model("content", "CaseStudy")
    template_product = apps.get_model("content", "TemplateProduct")

    for row in PUBLIC_CASES:
        defaults = row.copy()
        slug = defaults.pop("slug")
        case_study.objects.update_or_create(slug=slug, defaults=defaults)

    template_product.objects.filter(template_type="website").exclude(
        slug__in=ACTIVE_TEMPLATE_SLUGS
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0007_expand_public_services"),
    ]

    operations = [
        migrations.RunPython(sync_public_content, migrations.RunPython.noop),
    ]
