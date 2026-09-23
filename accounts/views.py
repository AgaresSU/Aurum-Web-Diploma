from django.contrib.auth import REDIRECT_FIELD_NAME, login
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .forms import EmailAuthenticationForm, RegistrationForm


def _redirect_after_login(request, value=""):
    if value and url_has_allowed_host_and_scheme(
        value,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return value
    return reverse("client_portal:dashboard")


def login_view(request):
    redirect_to = request.POST.get(REDIRECT_FIELD_NAME) or request.GET.get(REDIRECT_FIELD_NAME) or ""
    if request.user.is_authenticated:
        return redirect(_redirect_after_login(request, redirect_to))

    form = EmailAuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        return redirect(_redirect_after_login(request, redirect_to))

    return render(
        request,
        "accounts/login.html",
        {
            "form": form,
            "redirect_field_name": REDIRECT_FIELD_NAME,
            "redirect_field_value": redirect_to,
        },
    )


def register(request):
    redirect_to = request.POST.get(REDIRECT_FIELD_NAME) or request.GET.get(REDIRECT_FIELD_NAME) or ""
    if request.user.is_authenticated:
        return redirect(_redirect_after_login(request, redirect_to))

    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect(_redirect_after_login(request, redirect_to))

    return render(
        request,
        "accounts/register.html",
        {
            "form": form,
            "redirect_field_name": REDIRECT_FIELD_NAME,
            "redirect_field_value": redirect_to,
        },
    )
