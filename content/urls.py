from django.urls import path

from . import views


urlpatterns = [
    path('services/', views.services, name='services'),
    path('works/', views.works, name='works'),
    path('works/<slug:slug>/', views.work_detail, name='work_detail'),
]
