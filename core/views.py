from django.http import Http404
from django.shortcuts import render

from content.public_data import home_template_cards, services


def home(request):
    return render(
        request,
        'core/home.html',
        {
            'home_services': [item for item in services() if item.is_featured],
            'home_template_cards': home_template_cards(),
        },
    )


def missing_page(request, *args, **kwargs):
    raise Http404('Страница не найдена.')


def page_not_found(request, exception):
    return render(request, 'core/404.html', status=404)
