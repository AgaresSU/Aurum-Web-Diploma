from collections import defaultdict

CATEGORY_GROUPS = {
    "Панели управления и дашборды": "tech",
    "Авто и сервис": "auto",
    "B2B и производство": "b2b",
    "E-commerce": "ecommerce",
    "HR и команды": "education",
    "IT и разработка": "tech",
    "Еда и гостеприимство": "restaurant",
    "Красота и стиль": "beauty",
    "Локальные услуги": "local",
    "Маркетинг и продажи": "agency",
    "Медицина и здоровье": "clinic",
    "Недвижимость и строительство": "real_estate",
    "Образование и эксперты": "education",
    "События и креатив": "event",
    "Социальные проекты": "event",
    "Туризм и отдых": "travel",
    "Финансы и право": "legal",
    "Эксперты и консалтинг": "expert",
}

CATEGORY_GROUP_ORDER = (
    "legal",
    "restaurant",
    "clinic",
    "ecommerce",
    "real_estate",
    "tech",
    "expert",
    "beauty",
    "education",
    "b2b",
    "agency",
    "auto",
    "event",
    "local",
    "travel",
)


def interleave_template_cards(template_cards):
    grouped = defaultdict(list)
    for card in template_cards:
        category = card["template"].category
        grouped[CATEGORY_GROUPS.get(category, "local")].append(card)

    interleaved = []
    while any(grouped.values()):
        for group in CATEGORY_GROUP_ORDER:
            if grouped[group]:
                interleaved.append(grouped[group].pop(0))
        for group in tuple(grouped):
            if group not in CATEGORY_GROUP_ORDER and grouped[group]:
                interleaved.append(grouped[group].pop(0))
    return interleaved
