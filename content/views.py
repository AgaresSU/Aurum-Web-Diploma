from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from .models import Service, Work


def services(request):
    service_list = Service.objects.filter(is_published=True)
    return render(request, 'content/services.html', {'services': service_list})


def works(request):
    search_query = request.GET.get('q', '').strip()
    work_list = Work.objects.filter(is_published=True)
    if search_query:
        work_list = work_list.filter(
            Q(title__icontains=search_query)
            | Q(category__icontains=search_query)
            | Q(short_description__icontains=search_query)
        )
    return render(
        request,
        'content/works.html',
        {'works': work_list, 'search_query': search_query},
    )


def work_detail(request, slug):
    work = get_object_or_404(Work, slug=slug, is_published=True)
    return render(request, 'content/work_detail.html', {'work': work})
