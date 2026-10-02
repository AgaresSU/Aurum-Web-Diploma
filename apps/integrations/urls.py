from django.urls import path

from . import views

app_name = "integrations"

urlpatterns = [
    path("health/", views.health, name="health"),
    path("telegram/webhook/", views.telegram_webhook, name="telegram-webhook"),
    path("quick-telegram/webhook/", views.quick_telegram_webhook, name="quick-telegram-webhook"),
]
