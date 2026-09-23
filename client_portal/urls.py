from django.urls import path

from . import views


app_name = "client_portal"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("profile/", views.profile, name="profile"),
    path("profile/telegram/", views.telegram_connect, name="telegram-connect"),
    path("request/", views.create_request, name="create-request"),
    path("projects/", views.projects, name="projects"),
    path("projects/<uuid:token>/", views.project_detail, name="project-detail"),
    path("approvals/", views.approvals, name="approvals"),
    path("conversations/", views.conversations, name="conversations"),
    path("conversations/<uuid:token>/", views.unavailable_detail, name="conversation-detail"),
    path("orders/", views.orders, name="orders"),
    path("orders/<int:pk>/", views.unavailable_detail, name="order-detail"),
    path("invoices/", views.invoices, name="invoices"),
    path("invoices/<uuid:token>/", views.unavailable_detail, name="invoice-detail"),
    path("sites/", views.sites, name="sites"),
    path("sites/<int:pk>/", views.unavailable_detail, name="site-detail"),
]
