from django.shortcuts import render


WORKS = [
    {
        'slug': 'it-company',
        'name': 'Корпоративный сайт IT-компании',
        'category': 'Корпоративный сайт',
        'text': 'Сайт компании с рассказом об услугах, проектах и технологиях.',
        'image': 'images/examples/it-company-corporate.png',
        'demo': 'demos/it-company-corporate/index.html',
        'features': ['Главный экран', 'Раздел услуг', 'Форма обращения'],
    },
    {
        'slug': 'coffee-house',
        'name': 'Лендинг для кофейни',
        'category': 'Лендинг',
        'text': 'Одностраничный сайт кофейни с меню, преимуществами и контактами.',
        'image': 'images/examples/coffee-house-landing.png',
        'demo': 'demos/coffee-house-landing/index.html',
        'features': ['Главная страница', 'Меню', 'Контакты'],
    },
]


def services(request):
    service_list = [
        {
            'name': 'Сайт под ключ',
            'text': 'Сделаю сайт для компании, специалиста или небольшого проекта.',
        },
        {
            'name': 'Поддержка сайта',
            'text': 'Обновлю страницы, исправлю ошибки и помогу с доработками.',
        },
        {
            'name': 'Python-автоматизация',
            'text': 'Напишу небольшую программу для повторяющихся рабочих задач.',
        },
        {
            'name': 'Telegram-бот',
            'text': 'Сделаю бота для заявок, консультаций или уведомлений.',
        },
    ]
    return render(request, 'content/services.html', {'services': service_list})


def works(request):
    return render(request, 'content/works.html', {'works': WORKS})


def work_detail(request, slug):
    work = next((item for item in WORKS if item['slug'] == slug), None)
    if work is None:
        return render(request, 'core/404.html', status=404)
    return render(request, 'content/work_detail.html', {'work': work})
