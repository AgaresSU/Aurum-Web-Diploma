from django.urls import path

from . import views


urlpatterns = [
    path('', views.brief, name='brief'),
    path('success/', views.brief_success, name='brief_success'),
]
