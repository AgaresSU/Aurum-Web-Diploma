from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .forms import ProfileForm, RegistrationForm
from .models import Profile


def login_user(request):
    if request.user.is_authenticated:
        return redirect('accounts:account')

    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        login(request, form.get_user())
        return redirect('accounts:account')
    return render(request, 'accounts/login.html', {'form': form})


def register_user(request):
    if request.user.is_authenticated:
        return redirect('accounts:account')

    form = RegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, 'Аккаунт создан.')
        return redirect('accounts:account')
    return render(request, 'accounts/register.html', {'form': form})


@require_POST
def logout_user(request):
    logout(request)
    return redirect('home')


@login_required
def account(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    form = ProfileForm(request.POST or None, instance=profile)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Профиль сохранён.')
        return redirect('accounts:account')

    projects = request.user.projects.select_related('service')
    return render(request, 'accounts/account.html', {'form': form, 'projects': projects})
