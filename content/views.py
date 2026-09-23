from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render

from .public_data import catalog_context, find_service, find_template, services as public_services


def services(request):
    return render(request, 'content/services.html', {'services': public_services()})


def service_detail(request, slug):
    service = find_service(slug)
    if service is None:
        raise Http404('Услуга не найдена.')
    return render(request, 'content/service_detail.html', {'service': service})


def templates(request):
    category = request.GET.get('category', '').strip()
    return render(request, 'content/templates.html', catalog_context(category))


def reviews(request):
    return render(request, 'content/reviews.html', {'testimonials': (), 'review_form': None})


def template_demo(request, slug):
    if find_template(slug) is None:
        raise Http404('Пример сайта не найден.')
    return redirect('template-site-demo', slug=slug)


def template_site_demo(request, slug):
    if find_template(slug) is None:
        raise Http404('Пример сайта не найден.')
    demo = settings.BASE_DIR / 'core' / 'static' / 'demos' / slug / 'index.html'
    if not demo.is_file():
        raise Http404('Пример сайта не найден.')
    return redirect(f'/assets/demos/{slug}/index.html')
