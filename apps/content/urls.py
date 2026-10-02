from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "content"

urlpatterns = [
    path("services/", views.services, name="services"),
    path("services/<slug:slug>/", views.service_detail, name="service-detail"),
    path("reviews/", views.reviews, name="reviews"),
    path(
        "cases/",
        RedirectView.as_view(pattern_name="content:reviews", permanent=True),
        name="legacy-cases",
    ),
    path("templates/", views.templates, name="templates"),
    path("templates/<slug:slug>/demo/", views.template_demo, name="template-demo"),
]
