from django.urls import path

from . import views

app_name = "leads"

urlpatterns = [
    path("", views.create_lead, name="create"),
    path("templates/<slug:slug>/request/", views.create_template_request, name="template-request"),
]
