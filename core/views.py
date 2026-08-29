from django.shortcuts import render


def home(request):
    directions = [
        'Сайты под ключ',
        'Поддержка сайтов',
        'Python-автоматизация',
        'Telegram-боты',
    ]
    return render(request, 'core/index.html', {'directions': directions})
