from django.urls import path

from . import views

app_name = "messenger"

urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("api/messages/", views.create_message, name="create-message"),
    path("c/<uuid:token>/", views.public_conversation, name="public-conversation"),
]
