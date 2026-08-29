from django.http import HttpResponse


def home(request):
    return HttpResponse('<h1>AurumWeb</h1><p>Проект создан.</p>')
