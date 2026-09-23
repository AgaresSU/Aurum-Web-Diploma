from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from content.models import Service, Work

from .forms import LeadForm


def brief(request):
    work_slug = request.GET.get('project') or request.POST.get('project')
    work = get_object_or_404(Work, slug=work_slug, is_published=True) if work_slug else None

    initial = {}
    service_slug = request.GET.get('service')
    if service_slug:
        service = Service.objects.filter(slug=service_slug, is_published=True).first()
        if service:
            initial['service'] = service
    if work:
        initial['message'] = f'Интересует похожий проект: {work.title}'
    if request.user.is_authenticated:
        profile = getattr(request.user, 'profile', None)
        initial['name'] = profile.display_name if profile and profile.display_name else request.user.username
        initial['email'] = request.user.email

    form = LeadForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        lead = form.save(commit=False)
        lead.work = work
        if request.user.is_authenticated:
            lead.user = request.user
        lead.save()
        messages.success(request, 'Заявка отправлена. Я свяжусь с вами по указанному email.')
        return redirect('brief_success')

    return render(request, 'leads/brief.html', {'form': form, 'work': work})


def brief_success(request):
    return render(request, 'leads/success.html')
