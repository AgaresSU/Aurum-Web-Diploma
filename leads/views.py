from django.shortcuts import render

from content.public_data import find_template, templates

from .forms import PublicBriefForm


def _brief_initial(template):
    if template is None:
        return {
            'site_type': 'Корпоративный сайт',
            'goal': 'Получать заявки',
            'budget': 'Нужно оценить',
            'timeframe': 'Пока планируем',
        }
    return {
        'template_slug': template.slug,
        'site_type': 'Каталог услуг',
        'industry': template.industry or template.category,
        'goal': 'Получать заявки',
        'budget': 'Нужно оценить',
        'timeframe': 'Пока планируем',
        'task': '\n'.join(
            [
                f'Интересен пример «{template.title}».',
                'Нужно понять подходящий состав работ, сроки и поддержку после запуска.',
            ]
        ),
    }


def public_brief(request):
    template_items = templates()
    selected = find_template(request.POST.get('template_slug') or request.GET.get('template'))
    if request.method == 'POST':
        form = PublicBriefForm(request.POST, templates=template_items)
    else:
        form = PublicBriefForm(templates=template_items, initial=_brief_initial(selected))
    return render(
        request,
        'leads/public_brief.html',
        {
            'form': form,
            'selected_template': selected,
            'template_count': len(template_items),
        },
    )
