from django.shortcuts import render


def services(request):
    service_list = [
        {
            'name': 'Сайт под ключ',
            'text': 'Разработка сайта для компании, специалиста или проекта.',
        },
        {
            'name': 'Поддержка сайта',
            'text': 'Обновление страниц, исправление ошибок и развитие проекта.',
        },
        {
            'name': 'Python-автоматизация',
            'text': 'Небольшие программы для повторяющихся рабочих задач.',
        },
        {
            'name': 'Telegram-бот',
            'text': 'Бот для заявок, консультаций и уведомлений.',
        },
    ]
    return render(request, 'content/services.html', {'services': service_list})


def works(request):
    work_list = [
        'Сайт небольшой компании',
        'Лендинг для услуги',
        'Каталог примеров работ',
    ]
    return render(request, 'content/works.html', {'works': work_list})
