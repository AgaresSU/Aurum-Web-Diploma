from django.shortcuts import render


def home(request):
    directions = [
        {
            'name': 'Сайты под ключ',
            'text': 'Лендинги, корпоративные сайты и небольшие каталоги.',
        },
        {
            'name': 'Поддержка сайтов',
            'text': 'Обновляю страницы, исправляю ошибки и настраиваю резервные копии.',
        },
        {
            'name': 'Python-автоматизация',
            'text': 'Пишу программы для заявок, документов и повторяющихся задач.',
        },
        {
            'name': 'Telegram-боты',
            'text': 'Делаю ботов для консультаций, заявок и уведомлений.',
        },
    ]
    return render(request, 'core/index.html', {'directions': directions})
