from django.urls import path

from . import views


app_name = 'content'


urlpatterns = [
    path('services/', views.services, name='services'),
    path('services/<slug:slug>/', views.service_detail, name='service-detail'),
    path('reviews/', views.reviews, name='reviews'),
    path('templates/', views.templates, name='templates'),
    path('templates/<slug:slug>/demo/', views.template_demo, name='template-demo'),
]
