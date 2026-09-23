from copy import copy
from dataclasses import dataclass

from django.urls import reverse

from .catalog_order import interleave_template_cards
from .presentation import clean_service, clean_template_product, client_safe_category_filters
from .public_service_catalog import SERVICES
from .site_template_catalog import SITE_TEMPLATES


SERVICE_TYPE_LABELS = {
    "python": "Python-разработка",
    "website": "Сайт или лендинг",
    "support": "Поддержка сайтов",
    "telegram": "Telegram-боты",
    "seo": "SEO",
    "automation": "Автоматизация",
    "consulting": "Консалтинг",
    "integration": "Бизнес-интеграции",
}


@dataclass
class PublicService:
    title: str
    slug: str
    service_type: str
    short_description: str
    full_description: str
    business_value: str
    deliverables: str
    price_prefix: str
    price_amount: int | None
    price_unit: str
    price_note: str
    sort_order: int
    is_featured: bool
    is_published: bool

    def get_absolute_url(self):
        return reverse("content:service-detail", kwargs={"slug": self.slug})

    def get_service_type_display(self):
        return SERVICE_TYPE_LABELS.get(self.service_type, self.service_type)

    @property
    def formatted_price_amount(self):
        if self.price_amount is None:
            return ""
        return f"{self.price_amount:,}".replace(",", " ")

    @property
    def price_display(self):
        if self.price_amount is None:
            return "Расчет после разбора"
        parts = [self.price_prefix, f"{self.formatted_price_amount} ₽", self.price_unit]
        return " ".join(part for part in parts if part)


@dataclass
class PublicTemplate:
    title: str
    slug: str
    category: str
    industry: str
    short_description: str
    conversion_focus: str
    pages: str
    integrations: str
    sort_order: int


def services():
    items = [PublicService(**row) for row in SERVICES if row["is_published"]]
    return [clean_service(item) for item in sorted(items, key=lambda item: (item.sort_order, item.title))]


def templates():
    items = [
        PublicTemplate(**row, sort_order=100 + index)
        for index, row in enumerate(SITE_TEMPLATES, start=1)
    ]
    return [clean_template_product(item) for item in items]


def template_cards(items):
    return [
        {
            "template": copy(item),
            "preview_desktop_webp": f"template-previews/desktop-webp/{item.slug}.webp",
            "preview_mobile_webp": f"template-previews/mobile-webp/{item.slug}.webp",
        }
        for item in items
    ]


def home_template_cards():
    items = sorted(templates(), key=lambda item: (item.category, item.sort_order, item.title))[:12]
    return interleave_template_cards(template_cards(items))[:8]


def catalog_context(selected_category=""):
    all_items = templates()
    items = [item for item in all_items if not selected_category or item.category == selected_category]
    cards = template_cards(items)
    if not selected_category:
        cards = interleave_template_cards(cards)
    categories = sorted({item.category for item in all_items if item.category})
    return {
        "templates": cards,
        "template_count": len(items),
        "categories": client_safe_category_filters(categories),
        "selected_category": selected_category,
    }


def find_service(slug):
    return next((item for item in services() if item.slug == slug), None)


def find_template(slug):
    return next((item for item in templates() if item.slug == slug), None)
