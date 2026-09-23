def public_site(request):
    return {
        "canonical_url": request.build_absolute_uri(),
        "public_analytics": {},
        "public_og_image_url": request.build_absolute_uri("/assets/hero-premium.png"),
        "public_seller": {
            "name": "AurumWeb",
            "status": "Самозанятый исполнитель",
            "email": "aurumweb@aurumweb.ru",
        },
        "public_structured_data_json": "",
        "search_verification": {},
    }
