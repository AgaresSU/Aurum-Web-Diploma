from django.shortcuts import render

from content.models import Service


def home(request):
    directions = Service.objects.filter(is_published=True)[:4]
    return render(request, 'core/index.html', {'directions': directions})
