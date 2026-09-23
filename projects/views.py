from django.shortcuts import render

# Create your views here.
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .forms import ProjectForm
from .models import Project


@login_required
def project_list(request):
    search_query = request.GET.get('q', '').strip()
    projects = Project.objects.filter(owner=request.user).select_related('service')
    if search_query:
        projects = projects.filter(Q(title__icontains=search_query) | Q(summary__icontains=search_query))
    return render(
        request,
        'projects/project_list.html',
        {'projects': projects, 'search_query': search_query},
    )


@login_required
def project_detail(request, pk):
    project = get_object_or_404(Project.objects.select_related('service'), pk=pk, owner=request.user)
    return render(request, 'projects/project_detail.html', {'project': project})


@login_required
def project_create(request):
    form = ProjectForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        project = form.save(commit=False)
        project.owner = request.user
        project.save()
        messages.success(request, 'Проект добавлен.')
        return redirect('projects:detail', pk=project.pk)
    return render(request, 'projects/project_form.html', {'form': form, 'page_title': 'Новый проект'})


@login_required
def project_update(request, pk):
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    form = ProjectForm(request.POST or None, instance=project)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Изменения сохранены.')
        return redirect('projects:detail', pk=project.pk)
    return render(request, 'projects/project_form.html', {'form': form, 'page_title': 'Редактирование проекта'})


@login_required
def project_delete(request, pk):
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    if request.method == 'POST':
        project.delete()
        messages.success(request, 'Проект удалён.')
        return redirect('projects:list')
    return render(request, 'projects/project_confirm_delete.html', {'project': project})
