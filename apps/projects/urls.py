from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    path("files/<uuid:token>/download/", views.download_project_file, name="file-download"),
]
