import json

from django.conf import settings

from .seo import public_absolute_url, public_site_root


def public_seller(request):
    site_url = public_site_root(request)
    logo_url = public_absolute_url("/assets/brand/aurumweb-mark-squircle-new.png", request=request)
    seller_name = settings.AURUMWEB_SELLER_NAME.strip()
    seller_email = settings.AURUMWEB_PUBLIC_EMAIL.strip()
    seller_phone = settings.AURUMWEB_PUBLIC_PHONE.strip()
    seller_inn = settings.AURUMWEB_SELLER_INN.strip()
    organization = {
        "@type": "ProfessionalService",
        "@id": f"{site_url}/#organization",
        "name": "AurumWeb",
        "url": site_url,
        "logo": logo_url,
        "image": logo_url,
        "areaServed": "RU",
        "serviceType": [
            "Создание сайтов",
            "Поддержка сайтов",
            "Разработка Telegram-ботов",
            "Python-автоматизация",
        ],
    }
    if seller_email:
        organization["email"] = seller_email
    if seller_phone:
        organization["telephone"] = seller_phone
    if seller_name:
        organization["founder"] = {"@type": "Person", "name": seller_name}
    if seller_inn:
        organization["taxID"] = seller_inn
    structured_data = {
        "@context": "https://schema.org",
        "@graph": [
            organization,
            {
                "@type": "WebSite",
                "@id": f"{site_url}/#website",
                "url": site_url,
                "name": "AurumWeb",
                "inLanguage": "ru-RU",
                "publisher": {"@id": f"{site_url}/#organization"},
            },
        ],
    }
    return {
        "canonical_url": public_absolute_url(request.path, request=request),
        "public_og_image_url": logo_url,
        "public_site_url": site_url,
        "public_structured_data_json": json.dumps(structured_data, ensure_ascii=False).replace("</", "<\\/"),
        "public_seller": {
            "name": seller_name,
            "status": settings.AURUMWEB_SELLER_STATUS.strip(),
            "inn": seller_inn,
            "email": seller_email,
            "phone": seller_phone,
        },
        "search_verification": {
            "google": settings.AURUMWEB_GOOGLE_SITE_VERIFICATION,
            "yandex": settings.AURUMWEB_YANDEX_VERIFICATION,
        },
        "public_analytics": {
            "yandex_metrika_id": settings.AURUMWEB_YANDEX_METRIKA_ID,
        },
    }
